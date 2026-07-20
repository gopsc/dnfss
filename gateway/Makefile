VERSION := 0.0.1
APP_NAME := gateway
INSTL_DIR := /usr/local/bin
CPP := g++
INCS := -I inc/
MY_LIBS := -L/usr/lib -lus_http -lus_crypt
LIBS := -lcurl -lssl -lsqlite3 -lz -lcrypto -lboost_json -lboost_program_options
CPPFLAGS := --std=c++20 -pipe # -Wall -Werror
BUILD_DIR = build
$(shell mkdir -p $(BUILD_DIR))

#--------
all: $(BUILD_DIR)/$(APP_NAME).out
$(BUILD_DIR)/$(APP_NAME).out:	$(BUILD_DIR)/main.o
	$(CPP) $^ $(LIBS) $(MY_LIBS) -o $@
$(BUILD_DIR)/main.o:	src/main.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@
clear:
	@rm -rvf build/
