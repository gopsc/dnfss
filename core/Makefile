VERSION := 0.1.0

APP_NAME := nidus

INSTL_DIRS := /usr/local/bin

CONF_NAME := services.yaml

CONF_DIR := /etc/Nidus

SRV_NAME := Nidus.service

SRV_DIR := /etc/systemd/system

CPP:= g++

INCS:= -I include/

MY_LIBS := -L/usr/lib -lus_th -lus_http -lus_httpclient -lus_msg -lcurl
LIBS := -lpthread -lssl -lcrypto -lboost_program_options -lboost_json -lboost_thread -lboost_filesystem -lyaml-cpp

CPPFLAGS := --std=c++20 -O3 -pipe #-Wall -Werror

BUILD_DIR = build
$(shell mkdir -p $(BUILD_DIR))

#--------
all: ${APP_NAME}.out
${APP_NAME}.out: $(BUILD_DIR)/main.o $(BUILD_DIR)/PPool.o $(BUILD_DIR)/ProcessTask.o $(BUILD_DIR)/HttpTask.o $(BUILD_DIR)/ConfsTask.o $(BUILD_DIR)/Tttask.o $(BUILD_DIR)/pmc_mtd.o $(BUILD_DIR)/subsys_call.o $(BUILD_DIR)/InitParser.o
	$(CPP) $^ $(MY_LIBS) $(LIBS) -o $@

$(BUILD_DIR)/main.o:  src/main.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/PPool.o: src/main/PPool.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/ProcessTask.o: src/main/ProcessTask.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/Tttask.o: src/main/Tttask.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/HttpTask.o: src/main/HttpTask.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/ConfsTask.o: src/main/ConfsTask.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/pmc_mtd.o: src/main/pmc_mtd.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/subsys_call.o: src/main/subsys_call.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@

$(BUILD_DIR)/InitParser.o: src/registry/InitParser.cpp
	$(CPP) $(CPPFLAGS) -c $^ $(INCS) -o $@



install: $(APP_NAME).out
	@echo "正在安装 $(APP_NAME) 到$(INSTL_DIRS)"
	@
	#@mkdir -p $(INSTL_DIRS)
	@cp -f $(APP_NAME).out $(INSTL_DIRS)/$(APP_NAME)
	#@chmod 755 $(INSTL_DIRS)/pmcd
	@
	@echo "正在配置 $(APP_NAME)"
	@mkdir -p $(CONF_DIR)
	@cp $(CONF_NAME) $(CONF_DIR)/$(CONF_NAME)
	@cp init.txt $(CONF_DIR)/init.txt
	@cp $(SRV_NAME) $(SRV_DIR)/$(SRV_NAME)
	@systemctl daemon-reload
	@systemctl enable $(SRV_NAME)
	@systemctl restart $(SRV_NAME)
	@
	@echo "安装完成！"

uninstall:
	@echo "正在从 $(INSTL_DIRS) 卸载 $(APP_NAME)"
	@
	@rm -f $(INSTL_DIRS)/$(APP_NAME)
	@rm -f $(CONF_DIRS)/$(CONF_NAME)
	@rm -f $(CONF_DIRS)/init.txt
	@rm -f -d ${CONF_DIRS}
	@
	@systemctl stop $(SRV_NAME)
	@systemctl disable $(SRV_NAME)
	@rm -f $(SRV_DIRS)/$(SRV_NAME)
	@systemctl daemon-reload
	@
	@echo "卸载完成！"

clear:
	@rm -rvf $(BUILD_DIR)
	@rm -vf *.out
