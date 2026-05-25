import network
import socket
import time
import struct
import machine
from machine import I2C, Pin
from ds1307 import DS1307

# Constants
NTP_DELTA = 2208988800
NTP_HOST = "pool.ntp.org"
I2C_ADDR = 0x68

# Default DST rule (Europe)
def is_dst_europe(year, month, day):
    def last_sunday(month):
        for d in range(31, 24, -1):
            if time.gmtime(time.mktime((year, month, d, 0, 0, 0, 0, 0)))[:3] == (year, month, d):
                if time.gmtime(time.mktime((year, month, d, 0, 0, 0, 0, 0)))[6] == 6:
                    return d
        return 31

    start = last_sunday(3)
    end = last_sunday(10)

    if month > 3 and month < 10:
        return True
    if month == 3 and day >= start:
        return True
    if month == 10 and day < end:
        return True
    return False

# WiFi connect function
def connect_wifi(ssid, password, timeout=10):
    
    wlan = network.WLAN(network.STA_IF)
    wlan.disconnect()
    wlan.active(False)
    time.sleep(1)
    
    
    
    wlan.active(True)
    wlan.connect(ssid, password)

    while timeout > 0:
        if wlan.status() < 0 or wlan.status() >= 3:
            break
        timeout -= 1
        print('Waiting for WiFi...')
        time.sleep(1)

    if wlan.status() != 3:
        raise RuntimeError('WiFi connection failed')
    else:
        ip = wlan.ifconfig()[0]
        print(f'Connected, IP: {ip}')
        return wlan

# NTP sync function
def sync_time(use_ds1307=True, host=NTP_HOST, dst_rule=is_dst_europe):
    # NTP request
    NTP_QUERY = bytearray(48)
    NTP_QUERY[0] = 0x1B
    addr = socket.getaddrinfo(host, 123)[0][-1]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    try:
        s.settimeout(3)
        s.sendto(NTP_QUERY, addr)
        msg = s.recv(48)
    finally:
        s.close()

    val = struct.unpack("!I", msg[40:44])[0]
    t = val - NTP_DELTA
    tm = time.gmtime(t)

    # DST Offset
    t_offset = 1 if dst_rule(tm[0], tm[1], tm[2]) else 0
    print("DST offset:", t_offset)

    # Set internal RTC
    machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6] + 1, tm[3] + t_offset, tm[4], tm[5], 0))

    # Optionally sync to DS1307
    if use_ds1307:
        i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=800_000)
        ds1307 = DS1307(addr=I2C_ADDR, i2c=i2c)
        ds1307.datetime = time.localtime()

    print("System time set to:", time.localtime())
