import datetime
import json
import multiprocessing
import queue
import threading
import time
import gpiod
from pprint import pprint

import serial
from ctypes import c_int
from typing import Callable, List

from schemas import *
from utils import error_to_strings

heartbeat_send_interval = datetime.timedelta(milliseconds=500)
# expect to receive a heartbeat at least every second
heartbeat_receive_threshold = datetime.timedelta(milliseconds=1000)

print_status_interval = datetime.timedelta(seconds=2)

reset_line = 15


def _handle_communication(status_queue: multiprocessing.Queue, log_queue: multiprocessing.Queue,
                          duty_cycle: multiprocessing.Value, oven_state: multiprocessing.Value, serial_port: str,
                          baud_rate: int, should_exit: multiprocessing.Event, should_reset: multiprocessing.Event):
    while not should_exit.is_set():
        with serial.Serial(serial_port, baud_rate, timeout=1) as ser:
            last_send_time = time.monotonic()
            last_receive_time = time.monotonic()
            try:
                while not should_exit.is_set():
                    # check if serial data is available
                    if ser.in_waiting > 0:
                        # read a line
                        line = ser.readline().decode().strip()
                        if line:
                            try:
                                data = json.loads(line)
                                if 'current' in data:
                                    # status object
                                    parsed_data = OvenStatusSchema().load({
                                        "time": data['time'],
                                        "temperature": data['current'],
                                        "state": data['state'],
                                        "duty_cycle": data['pwm'],
                                        "door_open": data['door'] == 'open',
                                        "errors": error_to_strings(data['error'])
                                    })
                                    status_queue.put_nowait(parsed_data)
                                else:
                                    # log message
                                    parsed_data = LogMessageSchema().load({
                                        "message": data['message'],
                                        "severity": data['severity'],
                                        "time": data['time']
                                    })
                                    log_queue.put_nowait(parsed_data)
                                last_receive_time = time.monotonic()
                            except json.JSONDecodeError:
                                # log warning
                                pass
                            except queue.Empty:
                                # log warning
                                pass
                            except Exception as e:
                                # log error
                                pass
                        else:
                            # log warning, trigger reset
                            pass
                    elif (time.monotonic() - last_receive_time) >= heartbeat_receive_threshold.total_seconds():
                        # log warning
                        should_reset.set()

                    if (time.monotonic() - last_send_time) >= heartbeat_send_interval.total_seconds():
                        ser.write(json.dumps({'state': oven_state.value, 'pwm': duty_cycle.value}).encode())
                        last_send_time = time.monotonic()

                    time.sleep(0.1)
            except KeyboardInterrupt:
                print("tms keyboardinterrupt")
                should_exit.set()
            except serial.SerialException:
                print("tms serial exception")
            except Exception as e:
                print("tms exception")
                print(e)
        # log message "waiting 1 second before reconnecting"
        time.sleep(1)


class ThermalManagementSystem:
    __log_messages: List[LogMessageSchema] = []
    __oven_status: OvenStatusSchema = None

    __duty_cycle = multiprocessing.Value(c_int)
    __oven_state = multiprocessing.Value(c_int)
    __should_exit = multiprocessing.Event()
    __should_reset = multiprocessing.Event()

    __status_queue = multiprocessing.Queue()
    __log_queue = multiprocessing.Queue()

    __communication_process: multiprocessing.Process = None
    __monitor_thread: threading.Thread

    on_log_message: Callable[[LogMessageSchema], None] = None
    on_oven_status: Callable[[OvenStatusSchema], None] = None
    on_reset: Callable[[], None] = None

    def __init__(self, serial_port='/dev/ttyUSB0', baud_rate=115200,
                 on_log_message: Callable[[LogMessageSchema], None] = None,
                 on_oven_status: Callable[[OvenStatusSchema], None] = None,
                 on_reset: Callable[[], None] = None):

        self.on_log_message = on_log_message
        self.on_oven_status = on_oven_status
        self.on_reset = on_reset

        self.__communication_process = multiprocessing.Process(target=_handle_communication, args=(
            self.__status_queue, self.__log_queue, self.__duty_cycle, self.__oven_state, serial_port, baud_rate,
            self.__should_exit, self.__should_reset))

        self.__monitor_thread = threading.Thread(target=self.__monitor)
        self.__monitor_thread.start()
        self.__communication_process.start()

    def __del__(self):
        print('tms set should exit')
        self.__should_exit.set()
        print('tms comm process join')
        self.__communication_process.join()
        print('tms monitor join')
        self.__monitor_thread.join()
        print('tms del done')

    def __monitor(self):
        """
        Monitor the status and log queues and call the appropriate callbacks
        """
        with gpiod.request_lines('/dev/gpiochip2', consumer="reflow-backend", config={
            reset_line: gpiod.LineSettings(direction=gpiod.line.Direction.OUTPUT, output_value=gpiod.line.Value.ACTIVE)
        }) as gpio_request:

            last_print_status_time = time.monotonic()
            while (not self.__should_exit.is_set()) or not self.__status_queue.empty() or not self.__log_queue.empty():
                try:
                    status = self.__status_queue.get_nowait()
                    if (time.monotonic() - last_print_status_time) >= print_status_interval.total_seconds():
                        last_print_status_time = time.monotonic()
                        pprint(status)
                    if self.on_oven_status:
                        self.on_oven_status(status)
                    self.__oven_status = status
                except queue.Empty:
                    pass

                try:
                    log = self.__log_queue.get_nowait()
                    self.__log_messages.append(log)
                    pprint(log)
                    if self.on_log_message:
                        self.on_log_message(log)
                except queue.Empty:
                    pass

                if self.__should_reset.is_set():
                    gpio_request.set_value(reset_line, gpiod.line.Value.INACTIVE)
                    time.sleep(0.1)
                    gpio_request.set_value(reset_line, gpiod.line.Value.ACTIVE)
                    self.__should_reset.clear()
                    if self.on_reset:
                        self.on_reset()

                time.sleep(0.1)

    @property
    def log_messages(self):
        return self.__log_messages

    @property
    def oven_status(self) -> OvenStatusSchema:
        return self.__oven_status

    @property
    def oven_state(self) -> OvenState:
        return OvenState(self.__oven_state.value)

    def set_oven_state(self, value: OvenState):
        self.oven_state = value

    @oven_state.setter
    def oven_state(self, value: OvenState):
        self.__oven_state.value = value.value

    @property
    def duty_cycle(self) -> int:
        return self.__duty_cycle.value

    def set_duty_cycle(self, value: int):
        self.duty_cycle = value

    @duty_cycle.setter
    def duty_cycle(self, value: int):
        self.__duty_cycle.value = value

    def reset(self):
        self.__should_reset.set()
