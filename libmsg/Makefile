VERSION := 0.0.1
FRAMEWORK_NAME := us
LIB_NAME := libus_msg
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

$(BUILD_TDIR)/$(LIB_NAME).so: $(BUILD_TDIR)/Msg_Basic.o
	$(CPP) -shared $^ $(LIBS) -o $@

$(BUILD_TDIR)/Msg_Basic.o: src/Msg_Basic.cpp
	$(CPP) $(CPPFLAGS) -fPIC -c $^ $(INCS) -o $@

clear:
	@rm -rvf $(BUILD_TDIR)

install:
	@echo "install UniServe_libmsg..."
	@mkdir -vp $(INCLU_TDIR)/$(FRAMEWORK_NAME)
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/Msg_Basic.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/Msg.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/MsgServer.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/AsyncQueue.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/RpcClient.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/RpcServer.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@cp -vf $(BUILD_TDIR)/$(LIB_NAME).so $(INSTL_TDIR)/
	@echo "done."

uninstall:
	@echo "uninstall UniServe_libmsg..."
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/Msg_Basic.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/Msg.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/MsgServer.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/AsyncQueue.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/RpcClient.hpp
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/RpcServer.hpp
	@rm -vf $(INSTL_TDIR)/$(LIB_NAME).so
	@echo "done."
