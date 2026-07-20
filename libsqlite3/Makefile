VERSION := 0.0.1
FRAMEWORK_NAME := us
LIB_NAME := libus_sqlite3
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
#all: $(BUILD_TDIR)/$(LIB_NAME).so

#$(BUILD_TDIR)/$(LIB_NAME).so: $(BUILD_TDIR)/Thread.o
#	$(CPP) -shared $^ $(LIBS) -o $@

#$(BUILD_TDIR)/Thread.o: src/Thread.cpp
#	$(CPP) $(CPPFLAGS) -fPIC -c $^ $(INCS) -o $@

#clear:
#	@rm -rvf $(BUILD_TDIR)

install:
	@echo "install UniServe_libth..."
	@mkdir -vp $(INCLU_TDIR)/$(FRAMEWORK_NAME)
	@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/Sqlite3.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
#	@cp -vf $(BUILD_TDIR)/$(LIB_NAME).so $(INSTL_TDIR)/
	@echo "done."

uninstall:
	@echo "uninstall UniServe_libth..."
	@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/Sqlite3.hpp
#	@rm -vf $(INSTL_TDIR)/$(LIB_NAME).so
	@echo "done."
