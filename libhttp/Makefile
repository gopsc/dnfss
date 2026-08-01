VERSION := 0.0.1
FRAMEWORK_NAME := us
LIB_NAME := libus_http
LIBC_NAME := libus_httpclient
INSTL_TDIR := /lib
INCLU_TDIR := /usr/include
INCLU_SDIR := inc
CPP := g++
INCS := -I inc -I/opt/homebrew/include
CPPFLAGS := --std=c++20 -O2 -Wall -Werror
SERVER_LIBS := -L/opt/homebrew/lib -lssl -lcrypto -lpthread
CLIENT_LIBS := -L/opt/homebrew/lib -lssl -lcrypto -lcurl -lpthread
BUILD_TDIR := build
$(shell mkdir -p $(BUILD_TDIR))

# Server object files (HttpServer 拆分后)
SERVER_OBJS := $(BUILD_TDIR)/HttpServer.o \
               $(BUILD_TDIR)/HttpServer_Listener.o \
               $(BUILD_TDIR)/HttpServer_Routes.o \
               $(BUILD_TDIR)/HttpServer_Request.o \
               $(BUILD_TDIR)/HttpServer_Ws.o \
               $(BUILD_TDIR)/HttpServer_WsSession.o

#--------
all: $(BUILD_TDIR)/$(LIB_NAME).so $(BUILD_TDIR)/$(LIBC_NAME).so

# ---- Server library (HttpServer, no curl) ----
$(BUILD_TDIR)/$(LIB_NAME).so: $(SERVER_OBJS)
	$(CPP) -shared $^ $(SERVER_LIBS) -o $@

$(BUILD_TDIR)/HttpServer.o: src/HttpServer.cpp inc/us/detail/HttpServerImpl.hpp
	$(CPP) $(CPPFLAGS) -fPIC -c $< $(INCS) -o $@

$(BUILD_TDIR)/HttpServer_Listener.o: src/HttpServer_Listener.cpp inc/us/detail/HttpServerImpl.hpp
	$(CPP) $(CPPFLAGS) -fPIC -c $< $(INCS) -o $@

$(BUILD_TDIR)/HttpServer_Routes.o: src/HttpServer_Routes.cpp inc/us/detail/HttpServerImpl.hpp
	$(CPP) $(CPPFLAGS) -fPIC -c $< $(INCS) -o $@

$(BUILD_TDIR)/HttpServer_Request.o: src/HttpServer_Request.cpp inc/us/detail/HttpServerImpl.hpp
	$(CPP) $(CPPFLAGS) -fPIC -c $< $(INCS) -o $@

$(BUILD_TDIR)/HttpServer_Ws.o: src/HttpServer_Ws.cpp inc/us/detail/HttpServerImpl.hpp
	$(CPP) $(CPPFLAGS) -fPIC -c $< $(INCS) -o $@

$(BUILD_TDIR)/HttpServer_WsSession.o: src/HttpServer_WsSession.cpp inc/us/detail/HttpServerImpl.hpp
	$(CPP) $(CPPFLAGS) -fPIC -c $< $(INCS) -o $@

# ---- Client library (HttpClient, depends on curl + beast websocket) ----
$(BUILD_TDIR)/$(LIBC_NAME).so: $(BUILD_TDIR)/HttpClient.o
	$(CPP) -shared $^ $(CLIENT_LIBS) -o $@

$(BUILD_TDIR)/HttpClient.o: src/HttpClient.cpp
	$(CPP) $(CPPFLAGS) -fPIC -c $^ $(INCS) -o $@

clear:
	@rm -rvf $(BUILD_TDIR)

install: all
	@echo "install UniServe_libhttp..."
	@mkdir -vp $(INCLU_TDIR)/$(FRAMEWORK_NAME)
	@mkdir -vp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/detail
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/HttpServer.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/HttpClient.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/WebSocketServer.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/WebSocketClient.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/WebSocketClientBase.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/detail/HttpServerImpl.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/detail/
	@cp -vf $(BUILD_TDIR)/$(LIB_NAME).so $(INSTL_TDIR)/
	@cp -vf $(BUILD_TDIR)/$(LIBC_NAME).so $(INSTL_TDIR)/
	@echo "done."

uninstall:
	@echo "uninstall UniServe_libhttp..."
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/HttpServer.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/HttpClient.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/WebSocketServer.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/WebSocketClient.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/WebSocketClientBase.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/detail/HttpServerImpl.hpp
	@rm -vf $(INSTL_TDIR)/$(LIB_NAME).so
	@rm -vf $(INSTL_TDIR)/$(LIBC_NAME).so
	@echo "done."
