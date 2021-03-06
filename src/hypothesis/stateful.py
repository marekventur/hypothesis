# coding=utf-8

# Copyright (C) 2013-2015 David R. MacIver (david@drmaciver.com)

# This file is part of Hypothesis (https://github.com/DRMacIver/hypothesis)

# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at http://mozilla.org/MPL/2.0/.

# END HEADER

"""This module provides support for a stateful style of testing, where tests
attempt to find a sequence of operations that cause a breakage rather than just
a single value.

Notably, the set of steps available at any point may depend on the
execution to date.

"""

from __future__ import division, print_function, absolute_import, \
    unicode_literals

import inspect
import traceback
from random import Random
from unittest import TestCase
from collections import namedtuple

from hypothesis.core import find
from hypothesis.errors import Flaky, NoSuchExample, InvalidDefinition, \
    UnsatisfiedAssumption
from hypothesis.settings import Settings, Verbosity
from hypothesis.reporting import report, verbose_report, current_verbosity
from hypothesis.utils.show import show
from hypothesis.internal.compat import hrange, integer_types
from hypothesis.searchstrategy.misc import JustStrategy, \
    SampledFromStrategy
from hypothesis.searchstrategy.strategies import BadData, BuildContext, \
    SearchStrategy, strategy, check_length, check_data_type, \
    one_of_strategies
from hypothesis.searchstrategy.collections import TupleStrategy, \
    FixedKeysDictStrategy

Settings.define_setting(
    name='stateful_step_count',
    default=50,
    description="""
Number of steps to run a stateful program for before giving up on it breaking.
"""
)


class TestCaseProperty(object):  # pragma: no cover

    def __get__(self, obj, typ=None):
        if obj is not None:
            typ = type(obj)
        return typ._to_test_case()

    def __set__(self, obj, value):
        raise AttributeError('Cannot set TestCase')

    def __delete__(self, obj):
        raise AttributeError('Cannot delete TestCase')


class GenericStateMachine(object):

    """A GenericStateMachine is the basic entry point into Hypothesis's
    approach to stateful testing.

    The intent is for it to be subclassed to provide state machine descriptions

    The way this is used is that Hypothesis will repeatedly execute something
    that looks something like:

    x = MyStatemachineSubclass()
    for _ in range(n_steps):
        x.execute_step(x.steps().example())

    And if this ever produces an error it will shrink it down to a small
    sequence of example choices demonstrating that.

    """

    def steps(self):
        """Return a SearchStrategy instance the defines the available next
        steps."""
        raise NotImplementedError('%r.steps()' % (self,))

    def execute_step(self, step):
        """Execute a step that has been previously drawn from self.steps()"""
        raise NotImplementedError('%r.execute_steps()' % (self,))

    def print_step(self, step):
        """Print a step to the current reporter.

        This is called right before a step is executed.

        """
        self.step_count = getattr(self, 'step_count', 0) + 1
        report('Step #%d: %s' % (self.step_count, show(step)))

    def teardown(self):
        """Called after a run has finished executing to clean up any necessary
        state.

        Does nothing by default

        """
        pass

    @classmethod
    def find_breaking_runner(state_machine_class):
        def is_breaking_run(runner):
            try:
                runner.run(state_machine_class())
                return False
            except (InvalidDefinition, UnsatisfiedAssumption):
                raise
            except Exception:
                verbose_report(traceback.format_exc)
                return True
        return find(
            StateMachineSearchStrategy(
                state_machine_class.TestCase.settings
            ), is_breaking_run,
            state_machine_class.TestCase.settings,
        )

    _test_case_cache = {}

    TestCase = TestCaseProperty()

    @classmethod
    def _to_test_case(state_machine_class):
        try:
            return state_machine_class._test_case_cache[state_machine_class]
        except KeyError:
            pass

        class StateMachineTestCase(TestCase):
            settings = Settings()

            def runTest(self):
                try:
                    breaker = state_machine_class.find_breaking_runner()
                except NoSuchExample:
                    return

                breaker.run(state_machine_class(), print_steps=True)
                raise Flaky(
                    'Run failed initially by succeeded on a second try'
                )

        base_name = state_machine_class.__name__
        StateMachineTestCase.__name__ = str(
            base_name + '.TestCase'
        )
        StateMachineTestCase.__qualname__ = str(
            getattr(state_machine_class, '__qualname__', base_name) +
            '.TestCase'
        )
        state_machine_class._test_case_cache[state_machine_class] = (
            StateMachineTestCase
        )
        return StateMachineTestCase


def seeds(starting, n_steps):
    random = Random(starting)

    result = []
    for _ in hrange(n_steps):
        result.append(random.getrandbits(64))
    return result


# Sentinel value used to mark entries as deleted.
TOMBSTONE = [object(), 'TOMBSTONE FOR STATEFUL TESTING']


class StateMachineRunner(object):

    """A StateMachineRunner is a description of how to run a state machine.

    It contains values that it will use to shape the examples.

    """

    def __init__(
        self, parameter_seed, template_seed, n_steps,
        record=None, templates=None,
    ):
        self.parameter_seed = parameter_seed
        self.template_seed = template_seed
        self.n_steps = n_steps
        assert 0 <= n_steps <= 1000000

        self.templates = templates or seeds(template_seed, n_steps)
        assert len(self.templates) >= n_steps
        self.record = list(record or ())

    def __trackas__(self):
        return (
            StateMachineRunner,
            self.parameter_seed, self.template_seed,
            self.n_steps,
            [data[1] for data in self.record],
        )

    def __repr__(self):
        return (
            'StateMachineRunner(%d/%d steps)' % (
                len([t for t in self.record if t != TOMBSTONE]),
                self.n_steps,
            )
        )

    def run(self, state_machine, print_steps=None):
        if print_steps is None:
            print_steps = current_verbosity() >= Verbosity.debug

        try:
            for i in hrange(self.n_steps):
                strategy = state_machine.steps()

                template_set = False
                if i < len(self.record):
                    if self.record[i] is TOMBSTONE:
                        continue
                    _, data = self.record[i]
                    try:
                        template = strategy.from_basic(data)
                        template_set = True
                    except BadData:
                        pass
                if not template_set:
                    parameter = strategy.draw_parameter(Random(
                        self.parameter_seed
                    ))
                    template = strategy.draw_template(
                        BuildContext(Random(self.templates[i])), parameter)

                new_record = (
                    strategy, strategy.to_basic(template)
                )
                if i < len(self.record):
                    self.record[i] = new_record
                else:
                    self.record.append(new_record)

                value = strategy.reify(template)

                if print_steps:
                    state_machine.print_step(value)
                state_machine.execute_step(value)
        finally:
            state_machine.teardown()


class StateMachineSearchStrategy(SearchStrategy):

    def __init__(self, settings=None):
        self.program_size = (settings or Settings.default).stateful_step_count

    def __repr__(self):
        return 'StateMachineSearchStrategy()'

    def reify(self, template):
        return template

    def produce_parameter(self, random):
        return (
            random.getrandbits(64)
        )

    def produce_template(self, context, parameter_value):
        parameter_seed = parameter_value
        return StateMachineRunner(
            parameter_seed,
            context.random.getrandbits(64),
            n_steps=self.program_size,
        )

    def to_basic(self, template):
        return [
            template.parameter_seed,
            template.template_seed,
            template.n_steps,
            [
                [data[1]]
                if data != TOMBSTONE else None
                for data in template.record
            ]
        ]

    def from_basic(self, data):
        check_data_type(list, data)
        check_length(4, data)
        check_data_type(integer_types, data[0])
        check_data_type(integer_types, data[1])
        check_data_type(integer_types, data[2])
        check_data_type(list, data[3])

        if data[2] < 0:
            raise BadData('Invalid negative number of steps: %d' % (
                data[2],
            ))
        if data[2] > Settings.default.stateful_step_count * 1000:
            raise BadData('Implausibly large number of steps: %d' % (
                data[2],
            ))

        record = []

        for record_data in data[3]:
            if record_data is None:
                record.append(TOMBSTONE)
            else:
                check_data_type(list, record_data)
                check_length(1, record_data)
                record.append((None, record_data[0]))
        return StateMachineRunner(
            parameter_seed=data[0], template_seed=data[1],
            n_steps=data[2],
            record=record,
        )

    def simplifiers(self, random, template):
        yield self.cut_steps
        yield self.random_discards
        yield self.delete_elements
        for i in hrange(len(template.record)):
            if template.record[i] != TOMBSTONE:
                strategy, data = template.record[i]
                if strategy is None:
                    continue
                child_template = strategy.from_basic(data)
                for simplifier in strategy.simplifiers(random, child_template):
                    yield self.convert_simplifier(strategy, simplifier, i)

    def convert_simplifier(self, strategy, simplifier, i):
        def accept(random, template):
            if i >= len(template.record):
                return
            if template.record[i] == TOMBSTONE:
                return
            try:
                reconstituted = strategy.from_basic(template.record[i][1])
            except BadData:
                return

            for t in simplifier(random, reconstituted):
                new_record = list(template.record)
                new_record[i] = (strategy, strategy.to_basic(t))
                yield StateMachineRunner(
                    parameter_seed=template.parameter_seed,
                    template_seed=template.template_seed,
                    templates=template.templates,
                    n_steps=template.n_steps,
                    record=new_record,
                )
        accept.__name__ = str('convert_simplifier(%s, %d)' % (
            simplifier.__name__, i
        ))
        return accept

    def random_discards(self, random, template):
        live = len([
            r for r in template.record if r != TOMBSTONE
        ])
        if live < 10:
            return

        for k in hrange(1, 8):
            for _ in hrange(10):
                new_record = list(template.record)
                for i in hrange(len(template.record)):
                    if new_record[i] != TOMBSTONE:
                        if random.randint(0, 9) <= k:
                            new_record[i] = TOMBSTONE
                yield StateMachineRunner(
                    parameter_seed=template.parameter_seed,
                    template_seed=template.template_seed,
                    templates=template.templates,
                    n_steps=template.n_steps,
                    record=new_record,
                )

    def cut_steps(self, random, template):
        if len(template.record) < template.n_steps:
            yield StateMachineRunner(
                parameter_seed=template.parameter_seed,
                template_seed=template.template_seed,
                templates=template.templates,
                n_steps=len(template.record),
                record=template.record,
            )
        mid = 0
        while True:
            next_mid = (template.n_steps + mid) // 2
            if next_mid == mid:
                break
            mid = next_mid
            yield StateMachineRunner(
                parameter_seed=template.parameter_seed,
                template_seed=template.template_seed,
                templates=template.templates,
                n_steps=mid,
                record=template.record,
            )
            new_record = list(template.record)
            for i in hrange(min(mid, len(new_record))):
                new_record[i] = TOMBSTONE
            yield StateMachineRunner(
                parameter_seed=template.parameter_seed,
                template_seed=template.template_seed,
                templates=template.templates,
                n_steps=template.n_steps,
                record=new_record,
            )

    def delete_elements(self, random, template):
        deletes = 0
        indices = list(hrange(len(template.record)))
        random.shuffle(indices)
        for i in indices:
            if deletes >= 10:
                break
            if template.record[i] != TOMBSTONE:
                deletes += 1
                new_record = list(template.record)
                new_record[i] = TOMBSTONE
                yield StateMachineRunner(
                    parameter_seed=template.parameter_seed,
                    template_seed=template.template_seed,
                    templates=template.templates,
                    n_steps=template.n_steps,
                    record=new_record,
                )


Rule = namedtuple(
    'Rule',
    ('targets', 'function', 'arguments')

)

Bundle = namedtuple('Bundle', ('name',))


RULE_MARKER = 'hypothesis_stateful_rule'


def rule(targets=(), target=None, **kwargs):
    """Decorator for RuleBasedStateMachine. Any name present in target or
    targets will define where the end result of this function should go. If
    both are empty then the end result will be discarded.

    targets may either be a Bundle or the name of a Bundle.

    kwargs then define the arguments that will be passed to the function
    invocation. If their value is a Bundle then values that have previously
    been produced for that bundle will be provided, if they are anything else
    it will be turned into a strategy and values from that will be provided.

    """
    if target is not None:
        targets += (target,)

    converted_targets = []
    for t in targets:
        while isinstance(t, Bundle):
            t = t.name
        converted_targets.append(t)

    def accept(f):
        if not hasattr(f, RULE_MARKER):
            setattr(f, RULE_MARKER, [])
        getattr(f, RULE_MARKER).append(
            Rule(
                targets=tuple(converted_targets), arguments=kwargs, function=f
            )
        )
        return f
    return accept


VarReference = namedtuple('VarReference', ('name',))


class SimpleSampledFromStrategy(SampledFromStrategy):

    def produce_parameter(self, random):
        return None

    def produce_template(self, context, parameter_value):
        return context.random.randint(0, len(self.elements) - 1)


class RuleBasedStateMachine(GenericStateMachine):

    """A RuleBasedStateMachine gives you a more structured way to define state
    machines.

    The idea is that a state machine carries a bunch of types of data
    divided into Bundles, and has a set of rules which may read data
    from bundles (or just from normal strategies) and push data onto
    bundles. At any given point a random applicable rule will be
    executed.

    """
    _rules_per_class = {}
    _base_rules_per_class = {}

    def __init__(self):
        if not self.rules():
            raise InvalidDefinition('Type %s defines no rules' % (
                type(self).__name__,
            ))
        self.bundles = {}
        self.name_counter = 1
        self.names_to_values = {}

    def __repr__(self):
        return '%s(%s)' % (
            type(self).__name__,
            show(self.bundles),
        )

    def upcoming_name(self):
        return 'v%d' % (self.name_counter,)

    def new_name(self):
        result = self.upcoming_name()
        self.name_counter += 1
        return result

    def bundle(self, name):
        return self.bundles.setdefault(name, [])

    @classmethod
    def rules(cls):
        try:
            return cls._rules_per_class[cls]
        except KeyError:
            pass

        for k, v in inspect.getmembers(cls):
            for r in getattr(v, RULE_MARKER, ()):
                cls.define_rule(
                    r.targets, r.function, r.arguments
                )
        cls._rules_per_class[cls] = cls._base_rules_per_class.pop(cls, [])
        return cls._rules_per_class[cls]

    @classmethod
    def define_rule(cls, targets, function, arguments):
        converted_arguments = {}
        for k, v in arguments.items():
            if not isinstance(v, Bundle):
                v = strategy(v)
            converted_arguments[k] = v
        if cls in cls._rules_per_class:
            target = cls._rules_per_class[cls]
        else:
            target = cls._base_rules_per_class.setdefault(cls, [])

        return target.append(
            Rule(targets, function, converted_arguments)
        )

    def steps(self):
        strategies = []
        for rule in self.rules():
            converted_arguments = {}
            valid = True
            for k, v in rule.arguments.items():
                if isinstance(v, Bundle):
                    bundle = self.bundle(v.name)
                    if not bundle:
                        valid = False
                        break
                    else:
                        v = SimpleSampledFromStrategy(bundle)
                converted_arguments[k] = v
            if valid:
                strategies.append(TupleStrategy((
                    JustStrategy(rule),
                    FixedKeysDictStrategy(converted_arguments)
                ), tuple))
        if not strategies:
            raise InvalidDefinition(
                'No progress can be made from state %r' % (self,)
            )
        return one_of_strategies(strategies)

    def print_step(self, step):
        rule, data = step
        data_repr = {}
        for k, v in data.items():
            if isinstance(v, VarReference):
                data_repr[k] = v.name
            else:
                data_repr[k] = show(v)
        self.step_count = getattr(self, 'step_count', 0) + 1
        report('Step #%d: %s%s(%s)' % (
            self.step_count,
            '%s = ' % (self.upcoming_name(),) if rule.targets else '',
            rule.function.__name__,
            ', '.join('%s=%s' % kv for kv in data_repr.items())
        ))

    def execute_step(self, step):
        rule, data = step
        data = dict(data)
        for k, v in data.items():
            if isinstance(v, VarReference):
                data[k] = self.names_to_values[v.name]
        result = rule.function(self, **data)
        if rule.targets:
            name = self.new_name()
            self.names_to_values[name] = result
            for target in rule.targets:
                self.bundle(target).append(VarReference(name))
