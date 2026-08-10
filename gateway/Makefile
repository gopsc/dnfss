VERSION := 0.9.0
APP_NAME := gateway
INSTL_DIR := /usr/local/bin
CPP := g++
INCS := -I inc/ -I ../libmsg/inc
MY_LIBS := -L/usr/lib -L../libmsg/build -lus_http -lus_httpclient -lus_crypt -lus_msg -lus_logc
LIBS := -pthread -lcurl -lssl -lz -lcrypto -lboost_json -lboost_program_options -Wl,-rpath,$(shell cd ../libmsg/build && pwd)
CPPFLAGS := --std=c++20 -pipe -pthread # -Wall -Werror
BUILD_DIR = build
$(shell mkdir -p $(BUILD_DIR))

#--------
all: $(BUILD_DIR)/$(APP_NAME).out
$(BUILD_DIR)/$(APP_NAME).out:	$(BUILD_DIR)/main.o
	$(CPP) $^ $(LIBS) $(MY_LIBS) -o $@
$(BUILD_DIR)/main.o:	src/main.cpp inc/Conf.hpp inc/ExpBase.hpp
	$(CPP) $(CPPFLAGS) -c $< $(INCS) -o $@
clear:
	@rm -rvf build/
