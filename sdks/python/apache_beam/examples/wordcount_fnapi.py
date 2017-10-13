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

"""A word-counting workflow using the experimental FnApi.

For the stable wordcount example see wordcount.py.
"""

# TODO(BEAM-2887): Merge with wordcount.py.

from __future__ import absolute_import

import argparse
import logging

import apache_beam as beam
from apache_beam.io import ReadFromText
# TODO(BEAM-2887): Enable after the issue is fixed.
# from apache_beam.io import WriteToText
from apache_beam.metrics import Metrics
from apache_beam.metrics.metric import MetricsFilter
from apache_beam.options.pipeline_options import DebugOptions
from apache_beam.options.pipeline_options import PipelineOptions


class WordExtractingDoFn(beam.DoFn):
  """Parse each line of input text into words."""

  def __init__(self):
    super(WordExtractingDoFn, self).__init__()
    self.words_counter = Metrics.counter(self.__class__, 'words')
    self.word_lengths_counter = Metrics.counter(self.__class__, 'word_lengths')
    self.word_lengths_dist = Metrics.distribution(
        self.__class__, 'word_len_dist')
    self.empty_line_counter = Metrics.counter(self.__class__, 'empty_lines')

  def process(self, element):
    """Returns an iterator over the words of this element.

    The element is a line of text.  If the line is blank, note that, too.

    Args:
      element: the element being processed

    Returns:
      The processed element.
    """

    # TODO(BEAM-3041): Move this import to top of the file after the fix.
    # Portable containers does not support save main session, and importing here
    # is required. This is only needed for running experimental jobs with FnApi.
    import re

    text_line = element.strip()
    if not text_line:
      self.empty_line_counter.inc(1)
    words = re.findall(r'[A-Za-z\']+', text_line)
    for w in words:
      self.words_counter.inc()
      self.word_lengths_counter.inc(len(w))
      self.word_lengths_dist.update(len(w))
    return words


def run(argv=None):
  """Main entry point; defines and runs the wordcount pipeline."""
  parser = argparse.ArgumentParser()
  parser.add_argument('--input',
                      dest='input',
                      default='gs://dataflow-samples/shakespeare/kinglear.txt',
                      help='Input file to process.')
  parser.add_argument('--output',
                      dest='output',
                      required=True,
                      help='Output file to write results to.')
  known_args, pipeline_args = parser.parse_known_args(argv)

  pipeline_options = PipelineOptions(pipeline_args)
  p = beam.Pipeline(options=pipeline_options)

  # Ensure that the experiment flag is set explicitly by the user.
  debug_options = pipeline_options.view_as(DebugOptions)
  use_fn_api = (
      debug_options.experiments and 'beam_fn_api' in debug_options.experiments)
  assert use_fn_api, 'Enable beam_fn_api experiment, in order run this example.'

  # Read the text file[pattern] into a PCollection.
  lines = p | 'read' >> ReadFromText(known_args.input)

  # Count the occurrences of each word.
  def count_ones(word_ones):
    (word, ones) = word_ones
    return (word, sum(ones))

  counts = (lines
            | 'split' >> (beam.ParDo(WordExtractingDoFn())
                          .with_output_types(str))
            | 'pair_with_one' >> beam.Map(lambda x: (x, 1))
            | 'group' >> beam.GroupByKey()
            | 'count' >> beam.Map(count_ones))

  # Format the counts into a PCollection of strings.
  def format_result(word_count):
    (word, count) = word_count
    return '%s: %s' % (word, count)

  # pylint: disable=unused-variable
  output = counts | 'format' >> beam.Map(format_result)

  # Write the output using a "Write" transform that has side effects.
  # pylint: disable=expression-not-assigned

  # TODO(BEAM-2887): Enable after the issue is fixed.
  # output | 'write' >> WriteToText(known_args.output)

  result = p.run()
  result.wait_until_finish()

  # Do not query metrics when creating a template which doesn't run
  if (not hasattr(result, 'has_job')    # direct runner
      or result.has_job):               # not just a template creation
    empty_lines_filter = MetricsFilter().with_name('empty_lines')
    query_result = result.metrics().query(empty_lines_filter)
    if query_result['counters']:
      empty_lines_counter = query_result['counters'][0]
      logging.info('number of empty lines: %d', empty_lines_counter.committed)

    word_lengths_filter = MetricsFilter().with_name('word_len_dist')
    query_result = result.metrics().query(word_lengths_filter)
    if query_result['distributions']:
      word_lengths_dist = query_result['distributions'][0]
      logging.info('average word length: %d', word_lengths_dist.committed.mean)


if __name__ == '__main__':
  logging.getLogger().setLevel(logging.INFO)
  run()
