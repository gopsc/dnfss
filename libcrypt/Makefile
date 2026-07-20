VERSION := 0.0.1
FRAMEWORK_NAME := us
LIB_NAME := libus_crypt
INSTL_TDIR := /lib
INCLU_TDIR := /usr/include
INCLU_SDIR := inc
CPP := g++
INCS := -I inc
CPPFLAGS := --std=c++20 -O3 -pipe -Wall -Werror
LIBS := 
BUILD_TDIR := build
$(shell mkdir -p $(BUILD_TDIR))

#--------
all: $(BUILD_TDIR)/$(LIB_NAME).so

$(BUILD_TDIR)/$(LIB_NAME).so: $(BUILD_TDIR)/Crypto_Basic.o
	$(CPP) -shared $^ $(LIBS) -o $@

$(BUILD_TDIR)/Crypto_Basic.o: src/Crypto_Basic.cpp
	$(CPP) $(CPPFLAGS) -fPIC -c $^ $(INCS) -o $@

clear:
	@rm -rvf $(BUILD_TDIR)

install:
	@echo "install UniServe_libcrypt..."
	@mkdir -vp $(INCLU_TDIR)/$(FRAMEWORK_NAME)
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/Crypto_Basic.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/Base64.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/SHA256.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/AES_CBC.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/MyRSA.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(BUILD_TDIR)/$(LIB_NAME).so $(INSTL_TDIR)/
	@echo "done."

uninstall:
	@echo "uninstall UniServe_libcrypt..."
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/Crypto_Basic.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/Base64.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/SHA256.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/AES_CBC.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/MyRSA.hpp
	@rm -vf $(INSTL_TDIR)/$(LIB_NAME).so
	@echo "done."
