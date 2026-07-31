VERSION := 0.0.1
FRAMEWORK_NAME := us
LIB_NAME := libus_http
INSTL_TDIR := /lib
INCLU_TDIR := /usr/include
INCLU_SDIR := inc
CPP := g++
INCS := -I inc -I/opt/homebrew/include
CPPFLAGS := --std=c++20 -O2 -Wall -Werror
LIBS := -L/opt/homebrew/lib -lssl -lcrypto -lcurl -lpthread
BUILD_TDIR := build
$(shell mkdir -p $(BUILD_TDIR))

#--------
all: $(BUILD_TDIR)/$(LIB_NAME).so

$(BUILD_TDIR)/$(LIB_NAME).so: $(BUILD_TDIR)/HttpServer.o #$(BUILD_TDIR)/WebSocketServer.o $(BUILD_TDIR)/WebSocketClient.o
	$(CPP) -shared $^ $(LIBS) -o $@

$(BUILD_TDIR)/HttpServer.o: src/HttpServer.cpp
	$(CPP) $(CPPFLAGS) -fPIC -c $^ $(INCS) -o $@

$(BUILD_TDIR)/WebSocketServer.o: src/WebSocketServer.cpp
	$(CPP) $(CPPFLAGS) -fPIC -c $^ $(INCS) -o $@

$(BUILD_TDIR)/WebSocketClient.o: src/WebSocketClient.cpp
	$(CPP) $(CPPFLAGS) -fPIC -c $^ $(INCS) -o $@

clear:
	@rm -rvf $(BUILD_TDIR)

install:
	@echo "install UniServe_libth..."
	@mkdir -vp $(INCLU_TDIR)/$(FRAMEWORK_NAME)
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/HttpServer.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/HttpClient.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	#@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/WebSocketServer.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	#@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/WebSocketClient.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(BUILD_TDIR)/$(LIB_NAME).so $(INSTL_TDIR)/
	@echo "done."

uninstall:
	@echo "uninstall UniServe_libth..."
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/HttpServer.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/HttpClient.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/WebSocketServer.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/WebSocketClient.hpp
	@rm -vf $(INSTL_TDIR)/$(LIB_NAME).so
	@echo "done."
