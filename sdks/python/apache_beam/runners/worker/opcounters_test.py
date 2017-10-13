from __future__ import division

import logging
import math
import random
import unittest
from builtins import object
from builtins import range

from past.utils import old_div

from apache_beam import coders
from apache_beam.runners.worker.opcounters import OperationCounters
from apache_beam.transforms.window import GlobalWindows
from apache_beam.utils.counters import CounterFactory

#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#



# Classes to test that we can handle a variety of objects.
# These have to be at top level so the pickler can find them.


class OldClassThatDoesNotImplementLen(object):  # pylint: disable=old-style-class

  def __init__(self):
    pass


class ObjectThatDoesNotImplementLen(object):

  def __init__(self):
    pass


class OperationCountersTest(unittest.TestCase):

  def verify_counters(self, opcounts, expected_elements, expected_size=None):
    self.assertEqual(expected_elements, opcounts.element_counter.value())
    if expected_size is not None:
      if math.isnan(expected_size):
        self.assertTrue(math.isnan(opcounts.mean_byte_counter.value()))
      else:
        self.assertEqual(expected_size, opcounts.mean_byte_counter.value())

  def test_update_int(self):
    opcounts = OperationCounters(CounterFactory(), 'some-name',
                                 coders.PickleCoder(), 0)
    self.verify_counters(opcounts, 0)
    opcounts.update_from(GlobalWindows.windowed_value(1))
    self.verify_counters(opcounts, 1)

  def test_update_str(self):
    coder = coders.PickleCoder()
    opcounts = OperationCounters(CounterFactory(), 'some-name',
                                 coder, 0)
    self.verify_counters(opcounts, 0, float('nan'))
    value = GlobalWindows.windowed_value('abcde')
    opcounts.update_from(value)
    estimated_size = coder.estimate_size(value)
    self.verify_counters(opcounts, 1, estimated_size)

  def test_update_old_object(self):
    coder = coders.PickleCoder()
    opcounts = OperationCounters(CounterFactory(), 'some-name',
                                 coder, 0)
    self.verify_counters(opcounts, 0, float('nan'))
    obj = OldClassThatDoesNotImplementLen()
    value = GlobalWindows.windowed_value(obj)
    opcounts.update_from(value)
    estimated_size = coder.estimate_size(value)
    self.verify_counters(opcounts, 1, estimated_size)

  def test_update_new_object(self):
    coder = coders.PickleCoder()
    opcounts = OperationCounters(CounterFactory(), 'some-name',
                                 coder, 0)
    self.verify_counters(opcounts, 0, float('nan'))

    obj = ObjectThatDoesNotImplementLen()
    value = GlobalWindows.windowed_value(obj)
    opcounts.update_from(value)
    estimated_size = coder.estimate_size(value)
    self.verify_counters(opcounts, 1, estimated_size)

  def test_update_multiple(self):
    coder = coders.PickleCoder()
    total_size = 0
    opcounts = OperationCounters(CounterFactory(), 'some-name',
                                 coder, 0)
    self.verify_counters(opcounts, 0, float('nan'))
    value = GlobalWindows.windowed_value('abcde')
    opcounts.update_from(value)
    total_size += coder.estimate_size(value)
    value = GlobalWindows.windowed_value('defghij')
    opcounts.update_from(value)
    total_size += coder.estimate_size(value)
    self.verify_counters(opcounts, 2, old_div(float(total_size), 2))
    value = GlobalWindows.windowed_value('klmnop')
    opcounts.update_from(value)
    total_size += coder.estimate_size(value)
    self.verify_counters(opcounts, 3, old_div(float(total_size), 3))

  def test_should_sample(self):
    # Order of magnitude more buckets than highest constant in code under test.
    buckets = [0] * 300
    # The seed is arbitrary and exists just to ensure this test is robust.
    # If you don't like this seed, try your own; the test should still pass.
    random.seed(1717)
    # Do enough runs that the expected hits even in the last buckets
    # is big enough to expect some statistical smoothing.
    total_runs = 10 * len(buckets)

    # Fill the buckets.
    for _ in range(total_runs):
      opcounts = OperationCounters(CounterFactory(), 'some-name',
                                   coders.PickleCoder(), 0)
      for i in range(len(buckets)):
        if opcounts.should_sample():
          buckets[i] += 1

    # Look at the buckets to see if they are likely.
    for i in range(10):
      self.assertEqual(total_runs, buckets[i])
    for i in range(10, len(buckets)):
      self.assertTrue(buckets[i] > 7 * total_runs / i,
                      'i=%d, buckets[i]=%d, expected=%d, ratio=%f' % (
                          i, buckets[i],
                          10 * total_runs / i,
                          old_div(buckets[i], (10.0 * total_runs / i))))
      self.assertTrue(buckets[i] < 14 * total_runs / i,
                      'i=%d, buckets[i]=%d, expected=%d, ratio=%f' % (
                          i, buckets[i],
                          10 * total_runs / i,
                          old_div(buckets[i], (10.0 * total_runs / i))))


if __name__ == '__main__':
  logging.getLogger().setLevel(logging.INFO)
  unittest.main()
