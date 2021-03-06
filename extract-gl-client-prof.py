#!/usr/bin/python
# -*- coding: utf-8 -*-

#
# extract-gl-client-prof.py
# written by Ben England 2015
# copyright is GNU GPL V3, for details read:
#   https://tldrlegal.com/license/gnu-general-public-license-v3-%28gpl-3%29#fulltext
#
# Note: this tool uses a snapshot of javascript code from this project:
#   https://github.com/distributed-system-analysis/pbench
# but we do not support any use of this software outside of the graphing 
# of the data generated below.
#
# script to read gluster client-side output retrieved every N seconds
# and generate operation rate graph from it
#
# NOTE: the tool creates a subdirectory just for each run of this analysis tool.
# the directory name is just the name of the log file
# with the suffix '_csvdir'
#
# to install:
#   - extract javascript code from this tarball
#      https://s3.amazonaws.com/ben.england/gvp-graph-javascript.tgz
#   - if the directory containing your gluster volume output log is different, create a
#   'static' symlink pointing to the static/ subdirectory you just extracted
#     in the subdirectories where .csv and .html files live, you will see a 
#     'static' softlink pointing to this symlink.
#
# input:
#  this script expects input data to look like what this script produces:
#
#  https://raw.githubusercontent.com/bengland2/parallel-libgfapi/master/gvp-client.sh
#
#  record 1 contains the user-specified sample count and interval
#  used by gvp-client.sh.
#  record 2 is a timestamp generated by gluster in format like:
#    Wed Oct 21 22:50:28 UTC 2015
#  subsequent "gluster volume profile your-volume info" outputs are 
#  concatenated to the profile log.  
#  Each profile sample is assumed to happen approximately N seconds after
#  the preceding sample, where N is the gvp.sh sampling interval.
#  seconds.  The first sample happens N seconds after the timestamp.
#
# output:
#
#  when we're all done reading in data,
#  we then print it out in a format suitable for spreadsheet-based graphing
#
#  since we use pbench javascript graphing, then
#  column 1 in the .csv is always the timestamp in milliseconds when
#  that sample took place.  This can be disabled with the environment variable
#  SKIP_PBENCH_GRAPHING.
#
#  the stat types are:
#  - pct-lat - percentage latency consumed by this FOP (file operation)
#  - avg-lat - average latency (usec)
#  - min-lat - minimum latency (usec)
#  - max-lat - maximum latency (usec)
#  - call-rate - how many FOP requests have been processed per second
#  for each category:
#  - for each stat type, show stat by FOP
#
# internals:
#
# the "intervals" array, indexed by interval number, stores results over time
# within each array element, we have IntervalProfile objects containing
# bytes read/written and a dictionary indexed by FOP name
# containing FopProfile instances to represent the per-FOP records
# in "gluster volume profile" output.
# the per-FOP dictionary is indexed by FOP name
#

import sys
import os
from os.path import join
import re
import time
import shutil
import collections

# fields in gluster volume profile output

stat_names = ['pct-lat', 'avg-lat', 'min-lat', 'max-lat', 'call-rate']
directions = ['MBps-read', 'MBps-written']
min_lat_infinity = 1.0e24

# this environment variable lets you graph .csv files using pbench

pbench_graphs = True
if os.getenv('SKIP_PBENCH_GRAPHING'): pbench_graphs = False

# this is the list of graphs that will be produced

graph_csvs = [
    ('MBps-written', 'MB/sec written to Gluster volume'), 
    ('MBps-read', 'MB/sec read from Gluster volume'),
    ('call-rate', 'FOP call rates'),
    ('pct-lat', 'percentage latency by FOP')
]

# all gvp.sh-generated profiles are expected to have these parameters
# we define them here to have global scope, and they are only changed
# by the input parser

start_time = None
expected_duration = None
expected_sample_count = None
sorted_fop_names = None
intervals = None

# this class stores per-fop statistics from gluster client profile output
# to compute stats for %latency and average latency across a set of bricks,
# we have to compute averages weighted by FOP calls
# We do this in two steps:
# - loop over set of instances and compute weighted sum (not average)
# - after loop, normalize using total calls


class FopProfile:

    def __init__(self, avg_lat, min_lat, max_lat, calls):
        self.avg_lat = avg_lat
        self.min_lat = min_lat
        self.max_lat = max_lat
        self.calls = calls
        self.pct_lat = 0.0  # will compute later

    def __str__(self):
        return '%6.2f, %8.0f, %8.0f, %8.0f, %d' % (
            self.pct_lat, self.avg_lat, self.min_lat, self.max_lat, self.calls)

    # append a single field to .csv record based on statistic type
    # use "-6.2f" instead of "%6.2f" so there are no leading spaces in record,
    # otherwise spreadsheet inserts colums at col. B

    def field2str(self, stat, duration):
        if stat == stat_names[0]:
            return '%-6.2f' % self.pct_lat
        elif stat == stat_names[1]:
            return '%8.0f' % self.avg_lat
        elif stat == stat_names[2]:
            if self.min_lat == min_lat_infinity:
                return ''  # don't confuse spreadsheet/user
            else:
                return '%8.0f' % self.min_lat
        elif stat == stat_names[3]:
            if self.max_lat == 0:
                return ''
            else:
                return '%8.0f' % self.max_lat
        elif stat == stat_names[4]:
            call_rate = self.calls / float(duration)
            return '%10.3f' % call_rate

    # accumulate weighted sum of component profiles, will normalize them later

    def accumulate(self, addend):
        self.avg_lat += (addend.avg_lat * addend.calls)
        self.calls += addend.calls
        if addend.calls > 0:
            self.max_lat = max(self.max_lat, addend.max_lat)
            self.min_lat = min(self.min_lat, addend.min_lat)

    # normalize weighted sum to get averages

    def normalize_sum(self):
        try:
            # totals will become averages
            self.avg_lat /= self.calls
        except ZeroDivisionError:  # if no samples, set these stats to zero
            self.pct_lat = 0.0
            self.avg_lat = 0.0

    # compute % latency for this FOP given total latency of all FOPs

    def get_pct_lat(self, total_lat):
        try:
            self.pct_lat = 100.0 * (self.avg_lat * self.calls) / total_lat
        except ZeroDivisionError:  # if no samples, set these stats to zero
            self.pct_lat = 0.0


class ProfileInterval:

    def __init__(self):
        self.bytes_read = None
        self.bytes_written = None
        self.duration = None
        self.fop_profiles = {}

    def __str__(self):
        return '%d, %d, %s, %s'%(
            self.bytes_read, self.bytes_written, 
            str(self.duration), [ str(f) + ' : ' + str(self.fop_profiles[f]) for f in self.fop_profiles ])


# if there is an error parsing the input...

def usage(msg):
    print('ERROR: %s' % msg)
    print('usage: extract-gl-client-prof.py your-gluster-client-profile.log')
    sys.exit(1)


# segregate .csv files into a separate output directory
# with pathname derived from the input log file with _csvdir suffix

def make_out_dir(path):
    dir_path = path + '_csvdir'
    try:
        if os.path.exists(dir_path):
            shutil.rmtree(dir_path)
        os.mkdir(dir_path)
    except IOError:
        usage('could not (re-)create directory ' + dir_path)
    return dir_path


# convert gvp-client.sh client profile output
# into a time series of per-fop results.

def parse_input(input_pathname):
    global start_time
    global expected_sample_interval
    global expected_sample_count
    global sorted_fop_names
    global intervals

    try:
        with open(input_pathname, 'r') as file_handle:
            lines = [ l.strip() for l in file_handle.readlines() ]
    except IOError:
        usage('could not read ' + input_pathname)
    tokens = lines[0].split()
    expected_sample_count = int(tokens[0])
    expected_sample_interval = int(tokens[1])
    start_time = time.mktime(
            time.strptime(
                    lines[1], '%a %b %d %H:%M:%S %Z %Y')) * 1000
    print('collection started at %s' % lines[1])
    print('sampling interval is %d seconds' % expected_sample_interval)
    print('expected sample count is %d samples' % expected_sample_count)

    # parse the file and record each cell of output in a way that lets you
    # aggregate across bricks later

    found_cumulative_output = False
    found_interval_output = False
    all_caps_name = re.compile('^[A-Z]{3,15}')
    fop_names = set()
    last_intvl = -2
    intvl = -1
    per_op_table = {}
    sample = -1
    intervals = []
    for ln in lines[2:]:
        tokens = ln.split()

        if ln.__contains__('Interval') and ln.__contains__('stats'):

            interval_number = int(tokens[2])
            assert intvl == last_intvl + 1
            last_intvl = intvl
            intvl += 1
            intvl_profile = ProfileInterval()
            intervals.append(intvl_profile)
            found_interval_output = True

        elif ln.__contains__('Cumulative Stats'):

            found_cumulative_output = True

        elif ln.__contains__('Duration :'):

            # we are at end of output for this brick and interval

            assert found_cumulative_output ^ found_interval_output
            duration = int(tokens[2])
            diff_from_expected = abs(duration - expected_sample_interval)
            if found_interval_output:
                if diff_from_expected > 1:
                    print(('WARNING: in sample %d the sample ' +
                           'interval %d deviates from expected value %d') %
                            (sample, duration, expected_sample_interval))
                fops_in_interval = intervals[intvl]
                fops_in_interval.duration = duration

        elif ln.__contains__('BytesRead'):

            if found_interval_output:
                intvl_profile = intervals[intvl]
                intvl_profile.bytes_read = int(tokens[2])

        elif ln.__contains__('BytesWritten'):

            if found_interval_output:
                intvl_profile = intervals[intvl]
                intvl_profile.bytes_written = int(tokens[2])

        elif ln.__contains__('Cumulative stats'):

                # this is the end of this sample

                found_interval_output = False
                found_cumulative_output = True

        elif ln.__contains__('Current open fd'):

                found_cumulative_output = False

        elif found_interval_output and all_caps_name.match(ln):

            # we found a record we're interested in,
            # accumulate table of data for each gluster function

            sample += 1
            intvl_profile = intervals[intvl]
            fop_name = tokens[0]
            fop_names.add(fop_name)
            new_fop_profile = FopProfile(
                    float(tokens[2]), float(tokens[4]), float(tokens[6]),
                    float(tokens[1]))
            try:
                fop_stats = intvl_profile.fop_profiles[fop_name]
                raise Exception('did not expect fop already defined: %s' %
                        str(intvl_profile))
            except KeyError:
                intvl_profile.fop_profiles[fop_name] = new_fop_profile
    sorted_fop_names = sorted(fop_names)


# generate timestamp_ms column for pbench 
# given starting time of collection, sampling interval and sample number

def gen_timestamp_ms(sample_index):
    return start_time + ((expected_sample_interval * sample_index) * 1000)


# generate denominator for call rate computation based on duration type

def get_interval(interval_index, duration_type = 'interval'):
    if duration_type == 'cumulative':
        return interval_index * float(expected_sample_interval)
    else:
        return float(expected_sample_interval)

# display bytes read and bytes written
# normalize to MB/s with 3 decimal places so 1 KB/s/brick will show

def gen_output_bytes(out_dir_path):
    bytes_per_MB = 1000000.0
    for direction in directions:
        # when we support cumulative data, then we can name files this way
        #direction_filename = duration_type + '_' + direction + '.csv'
        direction_filename = direction + '.csv'
        direction_pathname = join(out_dir_path, direction_filename)
        with open(direction_pathname, 'w') as transfer_fh:
            if pbench_graphs: 
                transfer_fh.write('timestamp_ms, ')
            transfer_fh.write('MB/s\n')
            for j in range(0, len(intervals)):
                if pbench_graphs:
                    transfer_fh.write('%d, ' % gen_timestamp_ms(j))
                rate_interval = get_interval(j) 
                interval_profile = intervals[j]
                if direction.__contains__('read'):
                    transfer = interval_profile.bytes_read
                else:
                    transfer = interval_profile.bytes_written
                transfer_fh.write('%-8.3f\n' % 
                    ((transfer/rate_interval)/bytes_per_MB))

# display per-FOP (file operation) stats,

def gen_per_fop_stats(out_dir_path, stat, duration_type='interval'):
    per_fop_filename = stat + '.csv'
    per_fop_path = join(out_dir_path, per_fop_filename)
    with open(per_fop_path, 'a') as fop_fh:
        hdr = ''
        if pbench_graphs:
            hdr += 'timestamp_ms, '
        hdr += ','.join(sorted_fop_names)
        hdr += '\n'
        fop_fh.write(hdr)
        for i in range(0, len(intervals)):
            interval_profile = intervals[i]
            fops_in_interval = interval_profile.fop_profiles
            all_fop_profile = FopProfile(0, 0, 0, 0)
            for fop in sorted_fop_names:
                fop_stats = fops_in_interval[fop]
                all_fop_profile.accumulate(fop_stats)
            all_fop_profile.normalize_sum()
            #print('intvl: %d' % i)
            #print('ALL FOPs: %s' % all_fop_profile)
            if pbench_graphs:
                fop_fh.write('%d, ' % gen_timestamp_ms(i))
            columns = []
            for fop in sorted_fop_names:
                fop_stats = fops_in_interval[fop]
                fop_stats.get_pct_lat(
                    all_fop_profile.avg_lat * all_fop_profile.calls)
                try:
                    fop_stats = fops_in_interval[fop]
                except KeyError:
                    fops_in_interval[fop] = fop_stats
                columns.append(
                    fop_stats.field2str(
                        stat, interval_profile.duration))
            fop_fh.write(','.join(columns) + '\n')

# generate graphs in 
# generate output files in separate directory from
# data structure returned by parse_input

next_graph_template='''
    <div class="chart">
      <h3 class="chart-header">%s
        <button id="save1">Save as Image</button>
        <div id="svgdataurl1"></div>
      </h3>
      <svg id="chart%d"></svg>
      <canvas id="canvas1" style="display:none"></canvas>
      <script>
        constructChart("lineChart", %d, "%s", 0.00);
      </script>
    </div>
'''

def output_next_graph(graph_fh, gr_index):
    (csv_filename, graph_description) = graph_csvs[gr_index]
    gr_index += 1  # graph numbers start at 1
    graph_fh.write( next_graph_template % (
                    graph_description, gr_index, gr_index, csv_filename))

# static content of HTML file

header='''
<!DOCTYPE HTML>
<html>
  <head>
    <meta charset="utf-8">
    <link href="static/css/v0.2/nv.d3.css" rel="stylesheet" type="text/css" media="all">
    <link href="static/css/v0.2/pbench_utils.css" rel="stylesheet" type="text/css" media="all">
    <script src="static/js/v0.2/function-bind.js"></script>
    <script src="static/js/v0.2/fastdom.js"></script>
    <script src="static/js/v0.2/d3.js"></script>
    <script src="static/js/v0.2/nv.d3.js"></script>
    <script src="static/js/v0.2/saveSvgAsPng.js"></script>
    <script src="static/js/v0.2/pbench_utils.js"></script>
  </head>
  <body class="with-3d-shadow with-transitions">
    <h2 class="page-header">summary profile of application activity on one client</h2>
'''

trailer='''
  </body>
</html>
'''


# generate graphs using header, trailer and graph template

def gen_graphs(out_dir_path):
    graph_path = join(out_dir_path, 'gvp-client-graphs.html')
    with open(graph_path, 'w') as graph_fh:
        graph_fh.write(header)
        for j in range(0, len(graph_csvs)):
            output_next_graph(graph_fh, j)
        graph_fh.write(trailer)
    return graph_path


# make link to where javascript etc lives in unpacked tarball
# ASSUMPTION is that output directory is a subdirectory of where this script
# lives (not a sub-subdirectory).  Sorry but that's the only way to generate a
# softlink that works when we copy the csvdir to a different location.

def gen_static_softlink(out_dir_path):
    saved_cwd = os.getcwd()
    static_dir = join(saved_cwd, 'static')
    if not os.path.exists(static_dir):
        print('ERROR: sorry, the javascript directory "static" ' + 
              'needs to be in same directory as this script, trying anyway...')
    os.chdir(out_dir_path)
    os.symlink(join('..', 'static'), 'static')
    os.chdir(saved_cwd)

# generate everything needed to view the graphs

def generate_output(out_dir_path):

    gen_output_bytes(out_dir_path)
    for s in stat_names:
        gen_per_fop_stats(out_dir_path, s)
    graph_path = gen_graphs(out_dir_path)
    gen_static_softlink(out_dir_path)

    sys.stdout.write('Gluster FOP types seen: ')
    for fop_name in sorted_fop_names:
        sys.stdout.write(' ' + fop_name)
    sys.stdout.write('\n')
    print('created Gluster statistics files in directory %s' % out_dir_path)
    if not os.path.isabs(graph_path):
        graph_path = join(os.getcwd(), graph_path)
    print('graphs now available at browser URL file://%s' % graph_path)


# the main program is kept in a subroutine so that it can run on Windows.

def main():
    if len(sys.argv) < 2:
        usage('missing gluster volume profile output log filename parameter'
              )
    fn = sys.argv[1]
    parse_input(fn)
    outdir = make_out_dir(fn)
    generate_output(outdir)

main()
