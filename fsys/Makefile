CPP      = g++
CPPFLAGS = -std=c++20 -O3 -Wall -Werror -pipe
INCS     = -I ../libhttp/inc -I ../libmsg/inc -I /opt/homebrew/include
LIBS     = -L ../libhttp/build -L ../libmsg/build -L /opt/homebrew/lib \
           -lus_http -lus_httpclient -lus_msg -lboost_json \
           -lssl -lcrypto -lcurl -lpthread
RPATH    = -Wl,-rpath,'$$ORIGIN/../../libhttp/build' \
           -Wl,-rpath,'$$ORIGIN/../../libmsg/build' \
           -Wl,-rpath,/opt/homebrew/lib

BUILD    = build
TARGET   = $(BUILD)/fsys
SRC      = src/fsys.cpp

all: $(TARGET)

$(BUILD):
	mkdir -p $(BUILD)

$(TARGET): $(SRC) | $(BUILD)
	$(CPP) $(CPPFLAGS) $(INCS) -o $@ $< $(LIBS) $(RPATH)

clean:
	rm -f $(TARGET)

.PHONY: all clean
