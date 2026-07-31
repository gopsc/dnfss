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

#--------
all: $(BUILD_TDIR)/$(LIB_NAME).so $(BUILD_TDIR)/$(LIBC_NAME).so

# ---- Server library (HttpServer, no curl) ----
$(BUILD_TDIR)/$(LIB_NAME).so: $(BUILD_TDIR)/HttpServer.o
	$(CPP) -shared $^ $(SERVER_LIBS) -o $@

$(BUILD_TDIR)/HttpServer.o: src/HttpServer.cpp
	$(CPP) $(CPPFLAGS) -fPIC -c $^ $(INCS) -o $@

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
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/HttpServer.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/HttpClient.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(BUILD_TDIR)/$(LIB_NAME).so $(INSTL_TDIR)/
	@cp -vf $(BUILD_TDIR)/$(LIBC_NAME).so $(INSTL_TDIR)/
	@echo "done."

uninstall:
	@echo "uninstall UniServe_libhttp..."
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/HttpServer.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/HttpClient.hpp
	@rm -vf $(INSTL_TDIR)/$(LIB_NAME).so
	@rm -vf $(INSTL_TDIR)/$(LIBC_NAME).so
	@echo "done."
