#!/usr/bin/env python

# import normal packages
import platform
import logging
import sys
import os
import requests
import sys
import urllib.parse
if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject
import sys
import time
import configparser  # for config/ini file
from functools import reduce

from datetime import datetime

# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__),
                '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService

class DbusEnvertechService:
    def __init__(self, servicename, paths, productname='Envertech Portal', connection='SolarmanV5 Modbus RTU'):
        config = self._getConfig()
        deviceinstance = int(config['DEFAULT']['Deviceinstance'])
        customname = config['DEFAULT']['CustomName']

        self._dbusservice = VeDbusService(
            "{}.tcp_{:02d}".format(servicename, deviceinstance))
        self._paths = paths

        logging.debug("%s /DeviceInstance = %d" %
                      (servicename, deviceinstance))

        # Create the management objects, as specified in the ccgx dbus-api document
        self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
        self._dbusservice.add_path(
            '/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
        self._dbusservice.add_path('/Mgmt/Connection', connection)

        # Create the mandatory objects
        self._dbusservice.add_path('/DeviceInstance', deviceinstance)
        # self._dbusservice.add_path('/ProductId', 16) # value used in ac_sensor_bridge.cpp of dbus-cgwacs
        # id assigned by Victron Support from SDM630v2.py
        self._dbusservice.add_path('/ProductId', 0xFFFF)
        self._dbusservice.add_path('/ProductName', productname)
        self._dbusservice.add_path('/CustomName', customname)
        self._dbusservice.add_path('/Connected', 1)

        self._dbusservice.add_path('/Latency', None)
        self._dbusservice.add_path(
            '/FirmwareVersion', self._getEnvertechVersion())
        self._dbusservice.add_path(
            '/HardwareVersion', self._getEnvertechHWVersion())
        self._dbusservice.add_path(
            '/Position', int(config['DEFAULT']['Position']))
        self._dbusservice.add_path('/Serial', self._getEnvertechSerial())
        self._dbusservice.add_path('/UpdateIndex', 0)
        # Dummy path so VRM detects us as a PV-inverter.
        self._dbusservice.add_path('/StatusCode', 0)

        # add path values to dbus
        for path, settings in self._paths.items():
            self._dbusservice.add_path(
                path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)

        # last update
        self._lastUpdate = 0

        # add _update function 'timer'
        # pause x ms before the next request
        updateInterval = int(config['DEFAULT']['UpdateInterval'])
        gobject.timeout_add(updateInterval*1000, self._update)

        # add _signOfLife 'timer' to get feedback in log every 5minutes
        gobject.timeout_add(self._getSignOfLifeInterval()
                            * 60*1000, self._signOfLife)

    def _getEnvertechSerial(self):
        config = self._getConfig()
        serial = config['DEFAULT']['Serial']
        return serial

    def _getEnvertechVersion(self):
        return self._getFirmwareVersion(None)
        # TODO read firmware version over Modbus
        # deye_data = self._getDeyeData()
        # fwVersion = deye_data['_firmwareVersion']
        # return fwVersion

    def _getEnvertechHWVersion(self):
        return 1.0
        # TODO read hardware version over Modbus

    def _getConfig(self):
        config = configparser.ConfigParser()
        config.read("%s/config.ini" %
                    (os.path.dirname(os.path.realpath(__file__))))
        return config

    def _getSignOfLifeInterval(self):
        config = self._getConfig()
        value = config['DEFAULT']['SignOfLifeLog']

        if not value:
            value = 0

        return int(value)

    def _getEnvertechData(self):
        config = self._getConfig()
        stationId = config['DEFAULT']['StationId'] 
        url = 'https://www.envertecportal.com/ApiStations/GetStationInfo'
        headers = {
        }
        data = {
            'stationId': stationId,
        }

        responseStationInfo = requests.post(url, headers=headers, data=data).json()

        url = 'https://www.envertecportal.com/ApiInverters/QueryTerminalReal'
        data = {
            'page': '1',
            'perPage': '1',
            'orderBy': 'GATEWAYSN',
            'whereCondition': '{"STATIONID":"' + stationId + '"}',
        }

        encoded_data = urllib.parse.urlencode(data)

        responseQueryInfo = requests.post(url, headers=headers, data=encoded_data).json()

        try:
            acEnergyForward = self._getDailyProduction(responseStationInfo)
            acPower = self._getTotalACOutputPower(responseStationInfo)
            acVoltage = self._getAcVoltage(responseQueryInfo)
            acCurrent = acPower / acVoltage
            firmwareVersion = self._getFirmwareVersion(responseStationInfo)
        except Exception as e:
            logging.critical('Error at %s', '_update', exc_info=e)

        return {
            "acEnergyForward": acEnergyForward,
            "acPower": acPower,
            "acCurrent": acCurrent,
            "acVoltage": acVoltage,
            "_firmwareVersion": firmwareVersion,
        }
    
    def _checkResetDailyProduction(self, response):
        unit_etoday = response['Data']['Etoday']
        oldValues = response.read_holding_registers(register_addr=0x0016, quantity=3)
        newValues = self._calcSystemTime()

        logging.debug('inverters system time: %s' %oldValues)
        logging.debug('new system time: %s' %newValues)

        if oldValues[0] != newValues[0] or oldValues[1]/256 != newValues[1]/256:
            logging.info('updating inverters system time')
            response.write_multiple_holding_registers(register_addr=0x0016, values=newValues)

            until = time.time() + 5 * 60
            while time.time() <= until:
                try:
                    dailyProduction = self._getDailyProduction(response)

                    if dailyProduction <= 0:
                        logging.info("successful reset of daily production")
                        return
                except Exception as e:
                    logging.critical('Error at %s', '_update', exc_info=e)

                time.sleep(5)

            logging.info("timeout on reset of daily production")

    def _calcSystemTime(self):
        now = datetime.now()
        ym = 256 * (now.year % 100) + now.month
        dh = 256 * now.day + now.hour
        ms = 256 * now.minute + now.second
        return [ym, dh, ms]
    
    
    def _getDailyProduction(self, response):
        return response['Data']['Etoday']

    def _getAcVoltage(self, response):
        return response['Data']['QueryResults'][0]['ACVOLTAGE']

    def _getGridCurrent(self, response):
        # TODO: get grid current from modbus
        return 0.0

    def _getTotalACOutputPower(self, response):
        return response['Data']['Power']
    def _getFirmwareVersion(self, response):
        # TODO get fw from modbus
        config = self._getConfig()
        firmwareVersion = config['DEFAULT']['FirmwareVersion']
        return firmwareVersion

    def _signOfLife(self):
        logging.info("--- Start: sign of life ---")
        logging.info("Last _update() call: %s" % (self._lastUpdate))
        logging.info("Last '/Ac/Power': %s" % (self._dbusservice['/Ac/Power']))
        logging.info("--- End: sign of life ---")
        return True

    def _update(self):
        try:
            # get data from deye
            envertech_data = self._getEnvertechData()

            config = self._getConfig()
            str(config['DEFAULT']['Phase'])

            pvinverter_phase = str(config['DEFAULT']['Phase'])

            # send data to DBus
            for phase in ['L1', 'L2', 'L3']:
                pre = '/Ac/' + phase

                if phase == pvinverter_phase:
                    self._dbusservice[pre + '/Voltage'] = envertech_data['acVoltage']
                    self._dbusservice[pre + '/Current'] = envertech_data['acCurrent']
                    self._dbusservice[pre + '/Power'] = envertech_data['acPower']
                    self._dbusservice[pre + '/Energy/Forward'] = envertech_data['acEnergyForward']

                else:
                    self._dbusservice[pre + '/Voltage'] = 0
                    self._dbusservice[pre + '/Current'] = 0
                    self._dbusservice[pre + '/Power'] = 0
                    self._dbusservice[pre + '/Energy/Forward'] = 0

            self._dbusservice['/Ac/Voltage'] = self._dbusservice['/Ac/' + pvinverter_phase + '/Voltage']
            self._dbusservice['/Ac/Current'] = self._dbusservice['/Ac/' + pvinverter_phase + '/Current']
            self._dbusservice['/Ac/Power'] = self._dbusservice['/Ac/' + pvinverter_phase + '/Power']
            self._dbusservice['/Ac/Energy/Forward'] = self._dbusservice['/Ac/' + pvinverter_phase + '/Energy/Forward']
            self._dbusservice['/Connected'] = 1
            
            # logging
            logging.debug("House Consumption (/Ac/Power): %s" %(self._dbusservice['/Ac/Power']))
            logging.debug("House Forward (/Ac/Energy/Forward): %s" %(self._dbusservice['/Ac/Energy/Forward']))
            logging.debug("---")

            # update lastupdate vars
            self._lastUpdate = time.time()
        except Exception as e:
            logging.critical('Error at %s', '_update', exc_info=e)

            try:
                if self._lastUpdate < (time.time() - 5 * 60):
                    self._dbusservice['/Connected'] = 0
            except Exception as e:
                logging.critical('Error at %s', '_update', exc_info=e)
 
        try:
            # increment UpdateIndex - to show that new data is available
            index = self._dbusservice['/UpdateIndex'] + 1  # increment index
            if index > 255:   # maximum value of the index
                index = 0       # overflow from 255 to 0
            self._dbusservice['/UpdateIndex'] = index
        except Exception as e:
            logging.critical('Error at %s', '_update', exc_info=e)

        # return true, otherwise add_timeout will be removed from GObject - see docs http://library.isr.ist.utl.pt/docs/pygtk2reference/gobject-functions.html#function-gobject--timeout-add
        return True

    def _handlechangedvalue(self, path, value):
        logging.debug("someone else updated %s to %s" % (path, value))
        return True  # accept the change


def main():
    # configure logging
    logging.basicConfig(format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S',
                        level=logging.INFO,
                        handlers=[
                            logging.FileHandler(
                                "%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                            logging.StreamHandler()
                        ])

    try:
        logging.info("Start")

        from dbus.mainloop.glib import DBusGMainLoop
        # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
        DBusGMainLoop(set_as_default=True)

        # formatting
        def _kwh(p, v): return (str(round(v, 2)) + 'kWh')
        def _a(p, v): return (str(round(v, 1)) + 'A')
        def _w(p, v): return (str(round(v, 1)) + 'W')
        def _v(p, v): return (str(round(v, 1)) + 'V')

        # start our main-service
        pvac_output = DbusEnvertechService(
            servicename='com.victronenergy.pvinverter',
            paths={
                # energy produced by pv inverter
                '/Ac/Energy/Forward': {'initial': None, 'textformat': _kwh},
                '/Ac/Power': {'initial': 0, 'textformat': _w},
                '/Ac/Current': {'initial': 0, 'textformat': _a},
                '/Ac/Voltage': {'initial': 0, 'textformat': _v},

                '/Ac/L1/Voltage': {'initial': 0, 'textformat': _v},
                '/Ac/L2/Voltage': {'initial': 0, 'textformat': _v},
                '/Ac/L3/Voltage': {'initial': 0, 'textformat': _v},
                '/Ac/L1/Current': {'initial': 0, 'textformat': _a},
                '/Ac/L2/Current': {'initial': 0, 'textformat': _a},
                '/Ac/L3/Current': {'initial': 0, 'textformat': _a},
                '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
                '/Ac/L2/Power': {'initial': 0, 'textformat': _w},
                '/Ac/L3/Power': {'initial': 0, 'textformat': _w},
                '/Ac/L1/Energy/Forward': {'initial': None, 'textformat': _kwh},
                '/Ac/L2/Energy/Forward': {'initial': None, 'textformat': _kwh},
                '/Ac/L3/Energy/Forward': {'initial': None, 'textformat': _kwh},
            })

        logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
        mainloop = gobject.MainLoop()
        mainloop.run()
    except Exception as e:
        logging.critical('Error at %s', 'main', exc_info=e)


if __name__ == "__main__":
    main()
