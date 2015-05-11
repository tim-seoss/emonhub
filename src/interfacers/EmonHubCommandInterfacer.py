"""class EmonHubCommandGenInterfacer

"""
import time
import Cargo
from pydispatch import dispatcher
from emonhub_interfacer import EmonHubInterfacer

class EmonHubCommandInterfacer(EmonHubInterfacer):

    def __init__(self, name):
        # Initialization
        super(EmonHubCommandInterfacer, self).__init__(name)
        
        self._name = name
        
        self._settings = {
            'subchannels': ['ch1'],
            'pubchannels': ['ch2'],
            'execute_every_secs': 30,
            'timeout': 8,
            'withshell': True,
            'command': 'echo "Total Power = 1234"',
            'command_regexes': ['Total Power\s*=\s([0-9\.,]+)'],
            'node': 31,
        };
        

    def run(self):
        last = time.time()
        # XXX Can we make this event driven instead?
        while not self.stop:
            # Read the input and process data if available
            
            now = time.time()
            if (now-last) > int(self._settings['execute_every_secs']):
                last = now
                s = self._settings
                
                withshell = s['withshell'].lower() in ['1', 'yes', 'y', 'true']
                self._log.debug(str(s['execute_every_secs']) +"s loop")
                rxc = Cargo.new_cargo()
                rxc.nodeid = s['node']
                import subprocess32
                try: 
                    # TODO, make timeout dynamic?  e.g. Accept double the average execution time, or loop time, which ever the least?
                    rxc.realdata = self.parser(subprocess32.check_output(s['command'], shell=withshell, timeout=float(s['timeout'])))
                    for channel in s["pubchannels"]:
                        dispatcher.send(channel, cargo=rxc)
                        self._log.debug(str(rxc.uri) + " Sent to channel' : " + str(channel))
                # TODO record any partial output via e.output?
                except (subprocess32.TimeoutExpired, subprocess32.CalledProcessError) as e:
                    self._log.warn(str(e))
                except (OSError) as e:
                    self._log.warn(str(e))
                      
            # Don't loop too fast
            time.sleep(0.1)
            # Action reporter tasks
            # self.action()


    def parser(self, cmdoutput):
        import re
        data = []


        for r in self._settings['command_regexes']:
            for m in re.finditer(r, cmdoutput):
                data.append(m.group(1))
                self._log.debug('Regex "' + r + ' ... got data ' + m.group(1))

        return data


    def receiver(self, cargo):
        pass
        
    
    def set(self, **kwargs):
        for key,setting in self._settings.iteritems():
            if key in kwargs.keys():
                # replace default
                self._settings[key] = kwargs[key]
        
        # Subscribe to internal channels   
        for channel in self._settings["subchannels"]:
            dispatcher.connect(self.receiver, channel)
            self._log.debug(self._name+" Subscribed to channel' : " + str(channel))
