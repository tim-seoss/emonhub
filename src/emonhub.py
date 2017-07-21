#!/usr/bin/env python

"""

  This code is released under the GNU Affero General Public License.

  OpenEnergyMonitor project:
  http://openenergymonitor.org

"""

from __future__ import print_function, division
import sys
import time
import logging
import logging.handlers
import signal
import argparse
import pprint
import os
try:
      import pymodbus
      pymodbus_found = True
except ImportError:
      pymodbus_found = False

try:
      import bluetooth
      bluetooth_found = True
except ImportError:
      bluetooth_found = False

if os.getenv('NOTIFY_SOCKET'):
    try:
        import sdnotify
        SDNOTIFY = True
    except ImportError:
        print("Looks like we're expected to implement sd_notify(3). Maybe:")
        print("***")
        print("apt install python-sdnotify")
        print("***")
        print("?")
        raise
else:
    SDNOTIFY = False

import emonhub_setup as ehs
import interfacers.emonhub_interfacer as ehi
import emonhub_coder as ehc

import interfacers.EmonHubSerialInterfacer
import interfacers.EmonHubJeeInterfacer
import interfacers.EmonHubSocketInterfacer
import interfacers.EmonHubPacketGenInterfacer
import interfacers.EmonHubMqttInterfacer
import interfacers.EmonHubTesterInterfacer
import interfacers.EmonHubEmoncmsHTTPInterfacer
import interfacers.EmonHubSmilicsInterfacer
import interfacers.EmonHubVEDirectInterfacer
import interfacers.EmonHubGraphiteInterfacer
import interfacers.EmonHubBMWInterfacer
import interfacers.EmonHubTx3eInterfacer

if bluetooth_found:
    import interfacers.EmonHubSMASolarInterfacer

if pymodbus_found:
    import interfacers.EmonModbusTcpInterfacer
    import interfacers.EmonFroniusModbusTcpInterfacer

ehi.EmonHubSerialInterfacer = interfacers.EmonHubSerialInterfacer.EmonHubSerialInterfacer
ehi.EmonHubJeeInterfacer = interfacers.EmonHubJeeInterfacer.EmonHubJeeInterfacer
ehi.EmonHubSocketInterfacer = interfacers.EmonHubSocketInterfacer.EmonHubSocketInterfacer
ehi.EmonHubPacketGenInterfacer = interfacers.EmonHubPacketGenInterfacer.EmonHubPacketGenInterfacer
ehi.EmonHubMqttInterfacer = interfacers.EmonHubMqttInterfacer.EmonHubMqttInterfacer
ehi.EmonHubTesterInterfacer = interfacers.EmonHubTesterInterfacer.EmonHubTesterInterfacer
ehi.EmonHubEmoncmsHTTPInterfacer = interfacers.EmonHubEmoncmsHTTPInterfacer.EmonHubEmoncmsHTTPInterfacer
ehi.EmonHubSmilicsInterfacer = interfacers.EmonHubSmilicsInterfacer.EmonHubSmilicsInterfacer
ehi.EmonHubVEDirectInterfacer = interfacers.EmonHubVEDirectInterfacer.EmonHubVEDirectInterfacer
ehi.EmonHubGraphiteInterfacer = interfacers.EmonHubGraphiteInterfacer.EmonHubGraphiteInterfacer
ehi.EmonHubBMWInterfacer = interfacers.EmonHubBMWInterfacer.EmonHubBMWInterfacer
ehi.EmonHubTx3eInterfacer = interfacers.EmonHubTx3eInterfacer.EmonHubTx3eInterfacer

if bluetooth_found:
    ehi.EmonHubSMASolarInterfacer = interfacers.EmonHubSMASolarInterfacer.EmonHubSMASolarInterfacer

if pymodbus_found:
    ehi.EmonModbusTcpInterfacer = interfacers.EmonModbusTcpInterfacer.EmonModbusTcpInterfacer
    ehi.EmonFroniusModbusTcpInterfacer = interfacers.EmonFroniusModbusTcpInterfacer.EmonFroniusModbusTcpInterfacer

"""class EmonHub

Monitors data inputs through EmonHubInterfacer instances,
and (currently) sends data to
target servers through EmonHubEmoncmsReporter instances.

Controlled by the user via EmonHubSetup

"""

class EmonHub(object):

    __version__ = "emonHub 'emon-pi' variant v1.2"

    def __init__(self, setup):
        """Setup an OpenEnergyMonitor emonHub.

        Interface (EmonHubSetup): User interface to the hub.

        """

        # Initialize exit request flag
        self._exit = False

        # Initialize setup and get settings
        self._setup = setup
        settings = self._setup.settings

        # Initialize logging
        self._log = logging.getLogger("EmonHub")
        self._set_logging_level('INFO', False)
        self._log.info("EmonHub %s" % self.__version__)
        self._log.info("Opening hub...")

        # Initialize Interfacers
        self._interfacers = {}

        # Update settings
        self._update_settings(settings)

    def run(self):
        """Launch the hub.

        Monitor the interfaces and process data.
        Check settings on a regular basis.

        """

        # Set signal handler to catch SIGINT and shutdown gracefully
        signal.signal(signal.SIGINT, self._sigint_handler)

        if SDNOTIFY:


            systemd_notifier = sdnotify.SystemdNotifier()
            # Inform systemd that we're up and running
            self._log.debug("sd_notify() service startup OK (READY=1)")
            systemd_notifier.notify("READY=1")

            watchdog_secs = 30.0 # default watchdog timeout interval
            watchdog_env = os.getenv('WATCHDOG_USEC')
            if watchdog_env:
                watchdog_env_secs = int(watchdog_env) / 1000000
                if 'systemd_watchdog_timeout_secs' in setup.settings['hub']:
                    self._log.warn("WATCHDOG_USEC set in systemd unit file (See WatchdogSec= entry in systemd.service man page).")
                    self._log.warn("Older systemd versions don't support changing this value with sd_notify()")
                    self._log.warn("Ignoring 'systemd_watchdog_timeout_secs = " + \
                            setup.settings['hub']['systemd_watchdog_timeout_secs'] + "' in emonhub.conf and instead...")
                self._log.warn("Using WATCHDOG_USEC=" + watchdog_env + \
                        " (" + str(watchdog_env_secs) + "s) from systemd unit file.")
                watchdog_secs = watchdog_env_secs
            else:
                if 'systemd_watchdog_timeout_secs' in setup.settings['hub']:
                    watchdog_secs = float(setup.settings['hub']['systemd_watchdog_timeout_secs'])
            if (watchdog_secs > 0.0):
                watchdog_last_notify_time = time.time()
                hb_ok = True
                hb_require_all_threads = True
                if 'systemd_heartbeat_require_all_threads' in setup.settings['hub']:
                    if setup.settings['hub']['systemd_heartbeat_require_all_threads'] != 'yes':
                        hb_require_all_threads = False
                # Start watchdog running:
                watchdog_setup = "WATCHDOG_USEC=" + str(int(watchdog_secs * 1000000))
                self._log.debug("sd_notify() sending: " + watchdog_setup)
                systemd_notifier.notify(watchdog_setup)
                systemd_notifier.notify("WATCHDOG=1")
            else:
                self._log.debug("sd_notify() watchdog disabled with 'systemd_watchdog_timeout_secs' setting = " + str(watchdog_secs))



        # Until asked to stop
        while not self._exit:

            # Run setup and update settings if modified
            self._setup.run()
            if self._setup.check_settings():
                self._update_settings(self._setup.settings)

            # For all Interfacers
            for I in self._interfacers.itervalues():
                # Check thread is still running
                if not I.isAlive():
                    #I.start()
                    self._log.warning(I.name + " thread is dead") # had to be restarted")
                    if SDNOTIFY and hb_require_all_threads:
                        hb_ok = False

            # Issue a heatbeat when we are more than 50% through a wd timeout period
            if SDNOTIFY and watchdog_secs > 0.0 and (time.time() - watchdog_last_notify_time) > (watchdog_secs / 2):
                if hb_ok:
                    self._log.debug("sd_notify() watchdog heartbeat")
                    systemd_notifier.notify("WATCHDOG=1")
                    watchdog_last_notify_time = time.time()
                else:
                    self._log.debug("Won't heartbeat - state bad")
                    if (time.time() - watchdog_last_notify_time) > (watchdog_secs + 1):
                        self._log.warn("Over watchdog limit, but not killed by systemd!  Maybe you are using an old version of systemd...")
                        self._log.warn("and need to set WatchdogSec= in the systemd unit file (edit /etc/systemd/systemd/emonhub.service)?")
                        self._log.warn("and then 'systemctl daemon-reload'?")



            # Sleep until next iteration
            time.sleep(0.2)

    def close(self):
        """Close hub. Do some cleanup before leaving."""

        self._log.info("Exiting hub...")

        for I in self._interfacers.itervalues():
            I.stop = True
            I.join()

        self._log.info("Exit completed")
        logging.shutdown()

    def _sigint_handler(self, signal, frame):
        """Catch SIGINT (Ctrl+C)."""

        self._log.debug("SIGINT received.")
        # hub should exit at the end of current iteration.
        self._exit = True

    def _update_settings(self, settings):
        """Check settings and update if needed."""

        # EmonHub Logging level
        if 'loglevel' in settings['hub']:
            self._set_logging_level(settings['hub']['loglevel'])
        else:
            self._set_logging_level()


        # Create a place to hold buffer contents whilst a deletion & rebuild occurs
        self.temp_buffer = {}

        # Interfacers
        for name in self._interfacers.keys():
            # Delete interfacers if not listed or have no 'Type' in the settings without further checks
            # (This also provides an ability to delete & rebuild by commenting 'Type' in conf)
            if not name in settings['interfacers'] or not 'Type' in settings['interfacers'][name]:
                pass
            else:
                try:
                    # test for 'init_settings' and 'runtime_setting' sections
                    settings['interfacers'][name]['init_settings']
                    settings['interfacers'][name]['runtimesettings']
                except Exception as e:
                    # If interfacer's settings are incomplete, continue without updating
                    self._log.error("Unable to update '" + name + "' configuration: " + str(e))
                    continue
                else:
                    # check init_settings against the file copy, if they are the same move on to the next
                    if self._interfacers[name].init_settings == settings['interfacers'][name]['init_settings']:
                        continue
            # Delete interfacers if setting changed or name is unlisted or Type is missing
            self._log.info("Deleting interfacer '%s' ", name)
            self._interfacers[name].stop = True
            del(self._interfacers[name])

        for name, I in settings['interfacers'].iteritems():
            # If interfacer does not exist, create it
            if name not in self._interfacers:
                try:
                    if not 'Type' in I:
                        continue
                    self._log.info("Creating " + I['Type'] + " '%s' ", name)
                    if I['Type'] in ('EmonModbusTcpInterfacer','EmonFroniusModbusTcpInterfacer') and not pymodbus_found :
                        self._log.error("Python module pymodbus not installed. unable to load modbus interfacer")
                    # This gets the class from the 'Type' string
                    interfacer = getattr(ehi, I['Type'])(name, **I['init_settings'])
                    interfacer.set(**I['runtimesettings'])
                    interfacer.init_settings = I['init_settings']
                    interfacer.start()
                except ehi.EmonHubInterfacerInitError as e:
                    # If interfacer can't be created, log error and skip to next
                    self._log.error("Failed to create '" + name + "' interfacer: " + str(e))
                    continue
                except Exception as e:
                    # If interfacer can't be created, log error and skip to next
                    self._log.error("Unable to create '" + name + "' interfacer: " + str(e))
                    continue
                else:
                    self._interfacers[name] = interfacer
            else:
                # Otherwise just update the runtime settings if possible
                if 'runtimesettings' in I:
                    self._interfacers[name].set(**I['runtimesettings'])

        if 'nodes' in settings:
            ehc.nodelist = settings['nodes']

    def _set_logging_level(self, level='WARNING', log=True):
        """Set logging level.

        level (string): log level name in
        ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')

        """

        # Ensure "level" is all upper case
        level = level.upper()

        # Check level argument is valid
        try:
            loglevel = getattr(logging, level)
        except AttributeError:
            self._log.error('Logging level %s invalid' % level)
            return False
        except Exception as e:
            self._log.error('Logging level %s ' % str(e))
            return False

        # Change level if different from current level
        if loglevel != self._log.getEffectiveLevel():
            self._log.setLevel(level)
            if log:
                self._log.info('Logging level set to %s' % level)


if __name__ == "__main__":

    # Command line arguments parser
    parser = argparse.ArgumentParser(description='OpenEnergyMonitor emonHub')

    # Configuration file
    parser.add_argument("--config-file", action="store",
                        help='Configuration file', default=sys.path[0]+'/../conf/emonhub.conf')
    # Log file
    parser.add_argument('--logfile', action='store', type=argparse.FileType('a'),
                        help='Log file (default: log to Standard error stream STDERR)')
    # Show settings
    parser.add_argument('--show-settings', action='store_true',
                        help='show settings and exit (for debugging purposes)')
    # Show version
    parser.add_argument('--version', action='store_true',
                        help='display version number and exit')
    # Parse arguments
    args = parser.parse_args()

    # Display version number and exit
    if args.version:
        print('emonHub %s' % EmonHub.__version__)
        sys.exit()

    # Logging configuration
    logger = logging.getLogger("EmonHub")
    if args.logfile is None:
        # If no path was specified, everything goes to sys.stderr
        loghandler = logging.StreamHandler()
    else:
        # Otherwise, rotating logging over two 5 MB files
        # If logfile is supplied, argparse opens the file in append mode,
        # this ensures it is writable
        # Close the file for now and get its path
        args.logfile.close()
        loghandler = logging.handlers.RotatingFileHandler(args.logfile.name,
                                                       'a', 5000 * 1024, 1)
    # Format log strings
    loghandler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-8s %(threadName)-10s %(message)s'))

    logger.addHandler(loghandler)

    # Initialize hub setup
    try:
        setup = ehs.EmonHubFileSetup(args.config_file)
    except ehs.EmonHubSetupInitError as e:
        logger.critical(e)
        sys.exit("Unable to load configuration file: " + args.config_file)

    if 'use_syslog' in setup.settings['hub']:
        if setup.settings['hub']['use_syslog'] == 'yes':
            syslogger = logging.handlers.SysLogHandler(address='/dev/log')
            syslogger.setFormatter(logging.Formatter(
                  'emonHub[%(process)d]: %(levelname)-8s %(threadName)-10s %(message)s'))
            logger.addHandler(syslogger)

    # If in "Show settings" mode, print settings and exit
    if args.show_settings:
        setup.check_settings()
        pprint.pprint(setup.settings)

    # Otherwise, create, run, and close EmonHub instance
    else:
        try:
            hub = EmonHub(setup)
        except Exception as e:
            sys.exit("Could not start EmonHub: " + str(e))
        else:
            hub.run()
            # When done, close hub
            hub.close()
