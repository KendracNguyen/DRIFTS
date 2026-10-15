# Host-side tests for the logic that does not need an ESP32.
#
# The rolling-window statistics and the BLE stream framing are plain C++;
# running them on a laptop catches the arithmetic bugs in seconds instead
# of needing a car. The NimBLE and ELMduino layers are not covered here
# and must be tested on hardware.
#
#   make        build and run everything
#   make clean

CXX      ?= g++
CXXFLAGS := -std=c++17 -Wall -Wextra -Wno-unused-parameter -I.
SRC      := ../drifts_esp32

.PHONY: all clean
all: test_trigger test_stream test_pin_test
	./test_trigger
	./test_stream
	./test_pin_test

test_trigger: test_trigger.cpp $(SRC)/trigger.cpp $(SRC)/trigger.h $(SRC)/config.h
	$(CXX) $(CXXFLAGS) -o $@ test_trigger.cpp $(SRC)/trigger.cpp

test_stream: test_stream.cpp $(SRC)/ble_stream.cpp $(SRC)/ble_stream.h
	$(CXX) $(CXXFLAGS) -o $@ test_stream.cpp $(SRC)/ble_stream.cpp

test_pin_test: test_pin_test.cpp $(SRC)/pin_test.h
	$(CXX) $(CXXFLAGS) -o $@ test_pin_test.cpp

clean:
	rm -f test_trigger test_stream test_pin_test
