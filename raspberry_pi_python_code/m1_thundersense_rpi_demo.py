from __future__ import division

import json
import subprocess
from datetime import datetime
from uuid import getnode
import socket
from time import sleep

import requests
from bluepy.btle import *
from requests.exceptions import ConnectionError, ReadTimeout

REST_WRITE_HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

LOGIN_INFO = {
    'login_id': 'thunderboard',
    'password': 'Samplepw1',
    'api_key': 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
}

ENDPOINT = 'https://api.mediumone.com'
DEVICE_ADDR = '01:02:03:04:05:06'
INTERVAL_SECONDS = 10
INTERVAL_SECONDS_ACCEL = 2
SLEEP_ON_RESET = 5
DEBUG = False
FIRMWARE_VERSION = '032618a'


BATT_SERVICE = '180F'
UI_SERVICE = 'fcb89c40-c600-59f3-7dc3-5ece444a401b'
MOTION_SERVICE = 'a4e649f4-4be5-11e5-885d-feff819cdc9f'  # Also called 'inertial measurment'
ENVIRONMENTAL_SERVICE = '181A'
GENERAL_ACCESS_SERVICE = '1800'
AIR_QUALITY_SERVICE = 'efd658ae-c400-ef33-76e7-91b00019103b'
IO_SERVICE = '1815'


ACCEL_CHAR = 'c4c1f6e2-4be5-11e5-885d-feff819cdc9f'
ORIENT_CHAR = 'b7c4b694-bee3-45dd-ba9f-f3b5e994f49a'
BATTERY_CHAR = "2a19"
TEMP_CHAR = "2a6e"
HUMIDITY_CHAR = "2a6f"
PRESSURE_CHAR = "2A6D"
COMMAND_CHAR = "71e30b8c-4131-4703-b0a0-b0bbba75856b"
CO2_CHAR = 'efd658ae-c401-ef33-76e7-91b00019103b'
VOC_CHAR = 'efd658ae-c402-ef33-76e7-91b00019103b'


LED_CHAR = "2a56"


def login(session, login_id, user_pass, api_key, debug = None):
    """
    Logs in to the sandbox as the user passed in
    :param session: Requests session to log in from
    :param login_id: API user to log in as
    :param user_pass: Password
    :param api_key: API key
    :param debug: Optional file to write to if you are in debug mode
    :return: nothing
    """
    user_dict = {
        "login_id": login_id,
        "password": user_pass,
        "api_key": api_key
    }
    if debug:
        debug.write("{}: Logging in. login ID {}, api key {}\n".format(datetime.utcnow(), login_id, api_key))

    session.post('{}/v2/login'.format(ENDPOINT), data=json.dumps(user_dict),
                 headers=REST_WRITE_HEADERS, timeout=30)


def create_event(session, stream, data, add_ip=False, debug = None):
    """
    Sends an event to the sandbox
    :param session: Requests session to post to
    :param stream: Stream to send the data to
    :param data: JSON data
    :param add_ip: String of an IP address. If included, is sent along with the data
    :param debug: Optional file to write to if you are in debug mode
    :return: nothing
    """
    all_data = {"event_data": data}
    if add_ip:
        all_data['add_client_ip'] = add_ip

    data = json.dumps(all_data)
    if debug:
        debug.write("{}: Sending event. data: {}".format(datetime.utcnow(), data))
    response = session.post('{}/v2/events/{}/'.format(ENDPOINT, stream) + LOGIN_INFO['login_id'], data=data,
                            headers=REST_WRITE_HEADERS, timeout = 30)
    if response.status_code != 200:
        login(session, LOGIN_INFO['login_id'], LOGIN_INFO['password'], LOGIN_INFO['api_key'])
        if debug:
            debug.write("{}: Sending event after logging in. data: {}".format(datetime.utcnow(), data))
        response = session.post('{}/v2/events/{}/'.format(ENDPOINT, stream) + LOGIN_INFO['login_id'], data=data,
                                headers=REST_WRITE_HEADERS, timeout = 30)
        if response.status_code != 200:
            print(response.content)
            if debug:
                debug.write("{}: Problem posting to cloud. response: {}".format(datetime.utcnow(), response.content))
            raise ConnectionError("Could not send to cloud, restarting\n")


def twos_comp(val, bits):
    if (val & (1 << (bits - 1))) != 0:
        val -= 1 << bits
    return val


class AccelerationDelegate(DefaultDelegate):
    """
    This class reads the acceleration data from the board as it comes in as notifications.
    We manually put in a limit of sending max 1 event containing acceleration data to the cloud to avoid using
    too many credits. We also calculate a min, max, and average as the data comes in.
    For more information see: https://ianharvey.github.io/bluepy-doc/delegate.html
    """
    def __init__(self, session, motionGATT, debug = None):
        DefaultDelegate.__init__(self)
        self.session = session
        self.motionGATT = motionGATT
        self.last_motion_detected = datetime.utcnow()

        self.x_vals = []
        self.y_vals = []
        self.z_vals = []

        self.x_max = None
        self.y_max = None
        self.z_max = None

        self.x_min = None
        self.y_min = None
        self.z_min = None

        self.debug = debug

    def handleNotification(self, cHandle, data):
        if cHandle == self.motionGATT and type(data) == str:
            x_accel = abs((twos_comp((ord(data[1]) << 8) + ord(data[0]), 16)) / 1000.)
            y_accel = abs((twos_comp((ord(data[3]) << 8) + ord(data[2]), 16)) / 1000.)
            z_accel = abs((twos_comp((ord(data[5]) << 8) + ord(data[4]), 16)) / 1000.)
            self.x_vals.append(x_accel)
            self.y_vals.append(y_accel)
            self.z_vals.append(z_accel)

            self.x_max = max(self.x_max, x_accel) if self.x_max else x_accel
            self.y_max = max(self.y_max, y_accel) if self.y_max else y_accel
            self.z_max = max(self.z_max, z_accel) if self.z_max else z_accel

            self.x_min = min(self.x_min, x_accel) if self.x_min else x_accel
            self.y_min = min(self.y_min, y_accel) if self.y_min else y_accel
            self.z_min = min(self.z_min, z_accel) if self.z_min else z_accel

            if (datetime.utcnow() - self.last_motion_detected).total_seconds() > INTERVAL_SECONDS_ACCEL:
                json_data = {
                    'x_min': self.x_min,
                    'y_min': self.y_min,
                    'z_min': self.z_min,
                    'x_max': self.x_max,
                    'y_max': self.y_max,
                    'z_max': self.z_max,
                    'x_avg': sum(self.x_vals) / len(self.x_vals),
                    'y_avg': sum(self.y_vals) / len(self.y_vals),
                    'z_avg': sum(self.z_vals) / len(self.z_vals)
                }

                try:
                    create_event(self.session, 'sensor_data', json_data)
                except ConnectionError as ce:
                    print("Connection error, resetting session: {}\n".format(ce.message))
                    if self.debug:
                        self.debug.write("Connection error, resetting session: {}\n".format(ce.message))
                        self.debug.flush()
                    self.session.close()
                    self.session = requests.session()
                    sleep(SLEEP_ON_RESET)
                except ReadTimeout as re:
                    print("Internet connection lost during read, resetting session: {}\n".format(re.message))
                    if self.debug:
                        self.debug.write("Internet connection lost during read, resetting session: {}\n".format(re.message))
                        self.debug.flush()
                    self.session.close()
                    self.session = requests.session()
                    sleep(SLEEP_ON_RESET)
                self.last_motion_detected = datetime.utcnow()
                self.x_vals = []
                self.y_vals = []
                self.z_vals = []

                self.x_max = None
                self.y_max = None
                self.z_max = None

                self.x_min = None
                self.y_min = None
                self.z_min = None

def get_lan_addr():
    """
    This gets the LAN address from ifconfig on a raspberry pi running full rasbian
    :return: String lap address if exists, else None
    """
    p1 = subprocess.Popen("/sbin/ifconfig", stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["grep", "inet addr:"], stdin=p1.stdout, stdout=subprocess.PIPE)
    p3 = subprocess.Popen(["grep", "-v", "127.0.0.1"], stdin=p2.stdout, stdout=subprocess.PIPE)
    p1.stdout.close()
    p2.stdout.close()
    result = p3.communicate()[0]
    p1.wait()
    p2.wait()
    split = result.split('inet addr:')
    if len(split) >=2 :
        addr = split[1].split(' ')
        if len(addr) >= 1:
            return addr[0]
    return None

def get_lan_addr_rpi_lite():
    """
    This gets the LAN address from ifconfig on a raspberry pi running rasbpian lite.
    :return: String lap address if exists, else None
    """
    p1 = subprocess.Popen("/sbin/ifconfig", stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["grep", "inet"], stdin=p1.stdout, stdout=subprocess.PIPE)
    p3 = subprocess.Popen(["grep", "-v", "127.0.0.1"], stdin=p2.stdout, stdout=subprocess.PIPE)
    p4 = subprocess.Popen(["grep", "-v", "inet6"], stdin=p3.stdout, stdout=subprocess.PIPE)
    p1.stdout.close()
    p2.stdout.close()
    p3.stdout.close()
    result = p4.communicate()[0]
    p1.wait()
    p2.wait()
    p3.wait()
    split = result.split('inet ')
    if len(split) >=2 :
        addr = split[1].split(' ')
        if len(addr) >= 1:
            return addr[0]
    return None

def send_initialization_event(session):
    """
    Sends the initialization event to Medium One once the pi has paired with the thundersense.
    :param session:
    :return:
    """
    print(socket.gethostname())
    lan = get_lan_addr()
    if not lan:
        lan = get_lan_addr_rpi_lite()
    initial_event = {
        'connected' : True,
        'lan_ip_address' : lan,
        'mac_address' : getnode(),
        'firmware_version' : FIRMWARE_VERSION,
        'device_id' : DEVICE_ADDR,
    }
    print(initial_event)
    create_event(session, 'device_data', initial_event, add_ip= True)

def run(ble, debug=None):
    """
    Once connected to the thundersense, tries to connect to Medium One through the internet. If it cannot connect,
    it will maintain the connection with the thundersense and keep trying to connect to the cloud until it is successful.
    After that, it collects the data and sends it to the cloud as long as the connection is maintained
    :param ble:
    :param debug:
    :return:
    """
    session = requests.session()
    while True: # Keep trying to send init event until you can connect
        try:
            send_initialization_event(session)
            break
        except ConnectionError as ce:
            print("Connection error, resetting session: {}\n".format(ce.message))
            if debug:
                debug.write("Connection error, resetting session: {}\n".format(ce.message))
                debug.flush()
            session.close()
            session = requests.session()
            sleep(INTERVAL_SECONDS)
        except ReadTimeout as re:
            print("Internet connection lost during read, resetting session: {}\n".format(re.message))
            if debug:
                debug.write("Internet connection lost during read, resetting session: {}\n".format(re.message))
                debug.flush()
            session.close()
            session = requests.session()
            sleep(SLEEP_ON_RESET)
    envService = ble.getServiceByUUID(ENVIRONMENTAL_SERVICE)
    battService = ble.getServiceByUUID(BATT_SERVICE)
    motionService = ble.getServiceByUUID(MOTION_SERVICE)
    airQualityService = ble.getServiceByUUID(AIR_QUALITY_SERVICE)
    io_service = ble.getServiceByUUID(IO_SERVICE)

    accel_chars = motionService.getCharacteristics(forUUID=ACCEL_CHAR)
    temperature_chars = envService.getCharacteristics(forUUID=TEMP_CHAR)
    humidity_chars = envService.getCharacteristics(forUUID=HUMIDITY_CHAR)
    pressure_chars = envService.getCharacteristics(forUUID=PRESSURE_CHAR)
    bat_chars = battService.getCharacteristics(forUUID=BATTERY_CHAR)
    co2_chars = airQualityService.getCharacteristics(forUUID=CO2_CHAR)
    voc_chars = airQualityService.getCharacteristics(forUUID=VOC_CHAR)
    light_chars = io_service.getCharacteristics(forUUID=LED_CHAR)


    ble.setDelegate(AccelerationDelegate(requests.session(), accel_chars[0].getHandle(), debug= debug))

    # Turn on acceleration data
    for accel_char in accel_chars:
        if 'NOTIFY' in accel_char.propertiesToString():
            setup_data = b"\x01\x00"
            notify_handle = accel_char.getHandle() + 1
            ble.writeCharacteristic(notify_handle, setup_data, withResponse=True)
    last_motion_detected = datetime.utcnow()
    while True:
        json_data = {}
        for bat_char in bat_chars:
            if bat_char.supportsRead():
                bat_data = bat_char.read()
                if type(bat_data) == str:
                    bat_data_value = ord(bat_data[0])
                    json_data['battery'] = bat_data_value
        for temperature_char in temperature_chars:
            if temperature_char.supportsRead():
                temperature_data = temperature_char.read()
                if type(temperature_data) == str:
                    temperature_data_value = ((twos_comp((ord(temperature_data[1]) << 8) + ord(temperature_data[0]),
                                                        16)) / 100. ) * 1.8 + 32.
                    json_data['temperature'] = temperature_data_value

        for humidity_char in humidity_chars:
            if humidity_char.supportsRead():
                humidity_data = humidity_char.read()
                if type(humidity_data) == str:
                    humidity_data_value = (twos_comp((ord(humidity_data[1]) << 8) + ord(humidity_data[0]), 16)) / 100.
                    json_data['humidity'] = humidity_data_value

        for pressure_char in pressure_chars:
            if pressure_char.supportsRead():
                # Unsigned int 32 bit
                pressure_data = pressure_char.read()
                if type(pressure_data) == str:
                    pressure_data_value = ((ord(pressure_data[3]) << 24) + (ord(pressure_data[2]) << 16) + (
                    ord(pressure_data[1]) << 8) + ord(pressure_data[0])) / 1000.
                    json_data['pressure'] = pressure_data_value

        for co2_char in co2_chars:
            if co2_char.supportsRead():
                # Unsigned int 16 bit
                co2_data = co2_char.read()
                if type(co2_data) == str:
                    co2_data_value = ((ord(co2_data[1]) << 8) + ord(co2_data[0]))
                    json_data['co2'] = co2_data_value

        for voc_char in voc_chars:
            if voc_char.supportsRead():
                # Unsigned int 16 bit
                voc_data = voc_char.read()
                if type(voc_data) == str:
                    voc_data_value = ((ord(voc_data[1]) << 8) + ord(voc_data[0]))
                    json_data['voc'] = voc_data_value
        if (datetime.utcnow() - last_motion_detected).total_seconds() > INTERVAL_SECONDS:
            # Blink light
            for light_char in light_chars:
                if "WRITE" in light_char.propertiesToString():
                    light_char.write("01".decode("hex"), True)
                    light_char.write("00".decode("hex"), True)
                    light_char.write("01".decode("hex"), True)
                    light_char.write("00".decode("hex"), True)
            try:
                create_event(session, 'sensor_data', json_data)
            except ConnectionError as ce:
                print("Connection error, resetting session: {}\n".format(ce.message))
                if debug:
                    debug.write("Connection error, resetting session: {}\n".format(ce.message))
                    debug.flush()
                session.close()
                session = requests.session()
                sleep(SLEEP_ON_RESET)
            except ReadTimeout as re:
                print("Internet connection lost during read, resetting session: {}\n".format(re.message))
                if debug:
                    debug.write("Internet connection lost during read, resetting session: {}\n".format(re.message))
                    debug.flush()
                session.close()
                session = requests.session()
                sleep(SLEEP_ON_RESET)
            last_motion_detected = datetime.utcnow()

while True:
    f = open('/m1/debug.txt', 'a') if DEBUG else None
    with open('/m1/login.txt', 'r') as config:
        login_info = config.read().splitlines()
        if len(login_info) >= 2:
            LOGIN_INFO['login_id'] = login_info[0]
            LOGIN_INFO['password'] = login_info[1]
            LOGIN_INFO['api_key'] = login_info[2]
            DEVICE_ADDR = login_info[3]
    ble = Peripheral()
    try:
        while True:
            try:
                ble.connect(DEVICE_ADDR, 'public')
                break
            except BTLEException as be:
                print("Could not connect to device : " + be.message)
                if DEBUG:
                    f.write("{}: Could not connect to device : {}\n".format(datetime.utcnow(), be.message))
                    f.flush()
                sleep(SLEEP_ON_RESET)
        run(ble, debug=f)
    except BTLEException as be:
        print("BTLE Exception: {}. Reconnecting to the board".format(be.message))
        try:
            ble.disconnect()
        except BTLEException as be2:
            print("{}: BTLE exception while disconnecting: {}. Continuing...".format(datetime.utcnow(), be2.message))
        if DEBUG:
            f.write("{}: BTLE Exception: {}. Reconnecting to the board\n".format(datetime.utcnow(), be.message))
            f.flush()
            f.close()
        sleep(SLEEP_ON_RESET)
    except Exception as e:
        err_type = type(e).__name__
        print("Unexpected error of type {}: {}".format(err_type, e.message))
        try:
            ble.disconnect()
        except BTLEException as be2:
            print("{}: BTLE exception while disconnecting after unexepcted error: {}. Continuing...".format(datetime.utcnow(), be2.message))
        if DEBUG:
            f.write("{}: Unexpected error of type {}: {}\n".format(datetime.utcnow(), err_type, e.message))
            f.flush()
            f.close()
        sleep(SLEEP_ON_RESET)
