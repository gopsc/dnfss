CPP      = g++
CPPFLAGS = -std=c++20 -O3 -Wall -Werror -pipe
INCS     = -I ../libmsg/inc
LIBS     = -L ../libmsg/build -lus_msg -lboost_json -lsqlite3
RPATH    = -Wl,-rpath,'$$ORIGIN/../../libmsg/build'

BUILD    = build
TARGET   = $(BUILD)/db2
SRC      = src/db2.cpp

all: $(TARGET)

$(BUILD):
	mkdir -p $(BUILD)

$(TARGET): $(SRC) | $(BUILD)
	$(CPP) $(CPPFLAGS) $(INCS) -o $@ $< $(LIBS) $(RPATH)

clean:
	rm -f $(TARGET)

.PHONY: all clean
