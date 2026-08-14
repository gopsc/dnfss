VERSION := 1.0.0
FRAMEWORK_NAME := us
LIB_NAME := libus_memdb
INSTL_TDIR := /lib
INCLU_TDIR := /usr/include
INCLU_SDIR := inc
CPP := g++
INCS := -I inc
CPPFLAGS := --std=c++20 -O3 -pipe -Wall -Werror
LIBS :=
BUILD_TDIR := build
$(shell mkdir -p $(BUILD_TDIR))



install:
	@echo "install UniServe_libmemdb..."
	@mkdir -vp $(INCLU_TDIR)/${FRAMEWORK_NAME}
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/memdb.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
	@echo "done."

uninstall:
	@echo "uninstall UniServe_libmemdb..."
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/memdb.hpp
	@echo "done."