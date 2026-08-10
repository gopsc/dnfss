# liblogc — 统一集中式日志客户端库
#
# 编译产物：libus_logc.so
# 依赖：libus_msg（libmsg）、boost_json
# 安装：头文件 → /usr/include/us/LogC.hpp
#        库文件 → /lib/libus_logc.so

CC := g++
STD := -std=c++20

VERSION      := 0.0.1
FRAMEWORK_NAME := us
LIB_NAME     := libus_logc
INSTL_TDIR   := /lib
INCLU_TDIR   := /usr/include

CFLAGS  := $(STD) -O2 -fPIC -Wall
LDFLAGS := -shared -lus_msg -lboost_json

SRC_DIR   := src
BUILD_DIR := build
INC_DIR   := inc

SRC := $(wildcard $(SRC_DIR)/*.cpp)
OBJ := $(SRC:$(SRC_DIR)/%.cpp=$(BUILD_DIR)/%.o)

.PHONY: clear all install uninstall

all: $(BUILD_DIR) $(BUILD_DIR)/$(LIB_NAME).so

install: $(BUILD_DIR)/$(LIB_NAME).so
	mkdir -p $(INCLU_TDIR)/$(FRAMEWORK_NAME)
	cp -r $(INC_DIR)/$(FRAMEWORK_NAME)/* $(INCLU_TDIR)/$(FRAMEWORK_NAME)
	cp $(BUILD_DIR)/$(LIB_NAME).so $(INSTL_TDIR)/$(LIB_NAME).so.$(VERSION)
	ln -sf $(LIB_NAME).so.$(VERSION) $(INSTL_TDIR)/$(LIB_NAME).so
	ldconfig

uninstall:
	rm -f $(INCLU_TDIR)/$(FRAMEWORK_NAME)/LogC.hpp
	rm -f $(INSTL_TDIR)/$(LIB_NAME).so
	rm -f $(INSTL_TDIR)/$(LIB_NAME).so.$(VERSION)

clear:
	rm -rf $(BUILD_DIR)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/$(LIB_NAME).so: $(OBJ)
	$(CC) $(OBJ) -o $@ $(LDFLAGS)

$(BUILD_DIR)/%.o: $(SRC_DIR)/%.cpp
	$(CC) $(CFLAGS) -I$(INC_DIR) -c $< -o $@
