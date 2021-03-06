## Introduction

The extract-glvolprof.py program is meant to assist with visualizing the performance of
a gluster volume, using the gluster volume profile command.  One of key concepts in Gluster is the FOP (File Operation).  This is the unit of work passed from the application down through the Gluster translator stack until it reaches the storage device.  FOP Types for file creation, reading, writing, and many others are observable with these tools.


Statistic types produced per FOP type by these scripts include:

- call rates - for example, how many requests of different types are made per sec
- % latency - what fraction of FOP response time is consumed by different FOP types
- avg. latency - average FOP response time
- minimum latency
- maximum latency

Where all latencies are in units of microseconds.

The profiling tools consist of a collection and extraction script.  Typically you run the collection script to collect the profile data on a Gluster client or server, and then copy the file to your local system to run the extraction tool, which is just a python text processing script and should run anywhere.

To install, after cloning this repo, get the javascript for the graphs from the [pbench repo](https://github.com/distributed-system-analysis/pbench) and run something like [this script](https://github.com/distributed-system-analysis/pbench/blob/master/web-server/deploy.example.bash).

It contains a tarball containing some javascript libraries that are used by the HTML file above and provide common code to read CSV files and produce graphs using the nvd3 library.  This code comes from the pbench project at:

https://github.com/distributed-system-analysis/pbench

These tools produce a subdirectory containing java-script graphs that can be viewed with a web browser, as well as .csv-format files that can be loaded into a spreadsheet, for example.  BTW, not everything works: e.g. the "Save as Image" button does not. Note also that the layout is crucial: the CSV subdirectory contains a
symlink "static", which points to the "static" subdirectory in the
main directory (which is where the javascript tarball was unpacked). If you change that structure, then the javascript files may not be found - you then will see no graphs.

# server-side profiling

Server-side profiling allows you to see activity across the entire Gluster volume for a specified number of periodic samples.  It also allows you to see variation in stats between bricks, which can help you identify hotspots in your system where load is unevenly distributed.  Results include:

* per-volume MB/s read and written
* per-brick MB/s read and written
* per-volume per-FOP latency stats + call rates
* per-brick per-FOP (File OPeration) latency stats + call rate

It consists of:

* gvp.sh: a bash script which runs the above command periodically for a number
of samples, storing the results in a file.
* extract_glvolprof.py: a python script that takes that output file
and massages it into a form that can be used for visualization & analysis 

One component of this directory is an HTML file that can be viewed in a
browser. The other is a bunch of CSV files containing the
data. These files can also be used with a spreadsheet application if
desired, to produce graphs that way

Copy the scripts to some Gluster server in your cluster, (i.e. where you can run gluster volume profile command) and run the gvp.sh script. As an illustration, let's say we want to run it every 60 seconds and 10 iterations
(10 minutes of operation) - in practice, you might want to
do that periodically, perhaps in a cron job, in order to see the behavior
of the cluster over time.

\# ./gvp.sh [VOLNAME] 10 60

Then run the extract script
on that output file:

\# python extract-glvolprof.py gvp.log

The output (a bunch of CSV files and an HTML summary page) is placed in a subdirectory called gvp.log\_csvdir. 

Then copy static folder into gvp.log\_csvdir:

\# cp -R ./static/* ./gvp.log\_csvdir/

To see the graphs, fire up a browser and point it to the URL that the extract script printed, pointing to gvp-graphs.html .

# client-side profiling

Client-side profiling allows you to see activity as close to the application as possible, at the top of the Gluster translator stack.  This is particularly useful for identifying response time problems for the application  related to Gluster activity.  For example, Gluster replication causes a single application WRITE FOP to be transformed into multiple WRITE FOPs at the bricks within the volume where the file data resides.  The response time for the application's WRITE request may be significantly different from the brick-level WRITE FOP latencies, because it incorporates the network response time and cannot complete before the brick-level WRITE FOPs complete.

Copy the scripts to some directory on your client (i.e. where mountpoint is), and run the gvp-client.sh script. As an illustration, let's say we want to run it every 10 seconds and 12 iterations
(roughly two minutes of operation) - in practice, you might want to
do that periodically, perhaps in a cron job, in order to see the behavior
of the cluster over time.

\# ./gvp-client.sh [VOLNAME] [MOUNTPOINT] [SAMPLE-AMOUNT] [SAMPLE-DURATION-IN-SEC]
\# ./gvp-client.sh vol1 /rhgs/client/vol1 12 10

By default, the output file is called <code>gvp-client-[Timestamp].log</code> and saved in /var/tmp/. Then run the extract script
on that output file:

\# python extract-gl-client-prof.py /var/tmp/gvp-client-[Timestamp].log

The output (a bunch of CSV files and an HTML summary page) is placed in
a subdirectory in /var/tmp named similar to the supplied log file.  

To see the graphs, fire up a browser and point it to the URL that the extract
script printed, pointing to gvp-client-graphs.html

# implementation notes

In order to take advantage of pbench javascript graphing, then column 1 in the .csv is always the timestamp in milliseconds when that sample took place. This can be disabled by defining the environment variable SKIP\_PBENCH\_GRAPHING.

# appendix: detailed list of FOPs

Here are all the file operation types that Gluster supports upstream as of November 2015.  Looking for developers to correct descriptions here.  The ones that are typically encountered are marked with the letter C:

* ACCESS - ?
* CREATE - C - create a file
* DISCARD - support for trim?
* ENTRYLK - lock a directory given its pathname?
* FALLOCATE - allocate space for file without actually writing to it
* FENTRYLK - lock a file given its handle
* FGETXATTR - C - get named extended attribute value for a file (handle)
* FINODELK - C - lock a file/directory for write/read
* FLUSH - ensure all written data is persistently stored
* FREMOVEXATTR - remove a named extended attribute from a file handle
* FSETATTR - set value of metadata field (which ones?) for a file (handle)
* FSETXATTR - C - set value of a named extended attribute for a file handle
* FSTAT - get standard metadata about a file given its file handle
* FSYNC - C - ensure all written data for a file is persistently stored
* FSYNCDIR - ensure all directory entries in directory are persistently stored
* FTRUNCATE - set file size to specified value, deallocating data beyond this point
* FXATTROP - C - used by AFR replication?
* GETXATTR - get value of named extended attribute
* INODELK - lock a directory for write or for read
* LINK - create a hard link
* LK - lock?
* LOOKUP - C - lookup file within directory
* MKDIR - C - create directory
* MKNOD - create device special file
* OPEN - C - open a file
* OPENDIR - C - open a directory (in preparation for READDIR)
* RCHECKSUM - ?
* READ - C - read data from a file
* READDIR - C - read directory entries from a directory
* READDIRP - C - read directory entries with standard metadata for each file (readdirplus)
* READLINK - get the pathname of a file that a symlink is pointing to
* RELEASE - C - let go of file handle (similar to close)
* RELEASEDIR - let go of directory handle (similar to close)
* REMOVEXATTR - remove a named extended attribute from a pathname?
* RENAME - C - rename a file
* RMDIR - C - remove a directory (assumes it is already empty)
* SETATTR - set field in standard file metadata for pathname
* SETXATTR - C - set named extended attribute value for file given pathname
* STAT - C - get standard metadata for file given pathname
* STATFS - get metadata for the filesystem
* SYMLINK - create a softlink to specified pathname
* TRUNCATE - truncate file at pathname to specified size
* UNLINK - C - delete file
* WRITE - C - write data to file
* XATTROP - ?
* ZEROFILL - write zeroes to the file in specified offset range
