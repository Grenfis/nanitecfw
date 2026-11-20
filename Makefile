# FiiO Echo Nano custom firmware: patches the official V1.7.0 image you supply.
#
#   make                 build out/nanitecfw-VERSION-DATE/NANOV170.IMG from stock + patches/ + src/
#   make FW=path/to/NANOV170.IMG   stock image somewhere else
#   make DATE=20260901   build date (default 20260825, one day after stock V1.7.0)
#   make FW=NANOV200.IMG DELETE_TRACK=1   build V2 with deletion from playback
#   make recovery        unmodified stock code dated 20260826 -> out/recovery/NANOV170.IMG
#   make check           is the input the expected stock image?
#   make info / verify / diff / clean

FW      ?= NANOV170.IMG
DATE    ?= 20260825
TETRIS_DIAGNOSTIC ?= 0
DELETE_TRACK ?= 0
PY      := python3
FWTOOL  := $(PY) tools/rknano_fw.py
STOCK_NAME := $(shell $(PY) -c 'from tools.rknano_fw import stock_name; print(stock_name("$(FW)") or "")')
ifneq ($(findstring V2.0.0,$(STOCK_NAME)),)
PATCH_DIR := patches/v2
BLOB :=
V2_PROFILE := 1
GEN_DIR := out/gen/v2
else
PATCH_DIR := patches
BLOB := out/gen/cfw_blob.patch out/gen/cfw_hooks.patch
V2_PROFILE := 0
GEN_DIR := out/gen
endif
ifeq ($(V2_PROFILE),1)
ifeq ($(origin DATE),file)
DATE := 20261104
endif
endif
VERSION := $(shell $(PY) -c 'from tools.rknano_fw import parse_patches; print(next(parse_patches(["@$(PATCH_DIR)/about_version.patch"]))[2].decode("utf-16le").rstrip("\0"))')
ifeq ($(DELETE_TRACK),1)
ifneq ($(V2_PROFILE),1)
$(error DELETE_TRACK=1 requires the official V2.0.0 image)
endif
VERSION := 2.0.0
DELETE_DIR := $(GEN_DIR)/delete-track
OUT ?= out/nanitecfw-delete-track-$(VERSION)-$(DATE)/$(notdir $(FW))
BASE_OUT := $(DELETE_DIR)/base/$(notdir $(FW))
else
OUT     ?= out/nanitecfw-$(VERSION)-$(DATE)/$(notdir $(FW))
BASE_OUT := $(OUT)
endif

PATCHES := $(sort $(wildcard $(PATCH_DIR)/*.patch))
ASM     := $(sort $(wildcard $(PATCH_DIR)/*.S))
GEN     := $(addprefix $(GEN_DIR)/,$(notdir $(ASM:.S=.patch)))
SRC     := $(wildcard src/*.c src/*.S src/*.h src/*.ld)
GAME_SRC := $(wildcard src/tetris/* third_party/tengine/*)
GAME    := $(GEN_DIR)/tetris_blob.patch
TETRIS_MODE_STAMP := $(GEN_DIR)/.tetris-mode-$(V2_PROFILE)-$(TETRIS_DIAGNOSTIC)

ifeq ($(filter grouped-target,$(.FEATURES)),)
$(error GNU make 4.3 or newer needed (on macOS: brew install make, then run gmake))
endif
ifeq ($(shell command -v arm-none-eabi-gcc),)
$(warning arm-none-eabi-gcc not found - see README "Requirements")
endif

.PHONY: all build recovery check verify info diff help clean

all: build

help:
	@sed -n 's/^#   //p' Makefile

check:
	$(FWTOOL) check $(FW)

ifeq ($(V2_PROFILE),1)
V2_BLOB :=
else
V2_BLOB := $(BLOB)
endif

build: check $(GEN) $(V2_BLOB) $(GAME)
	@mkdir -p $(dir $(BASE_OUT)) $(dir $(OUT))
	$(FWTOOL) patch $(FW) $(BASE_OUT) --date $(DATE) $(addprefix @,$(PATCHES) $(GEN) $(V2_BLOB) $(GAME))
ifeq ($(DELETE_TRACK),1)
	$(PY) tools/build_delete_preview.py $(FW) $(BASE_OUT) $(DELETE_DIR) --enable-writes
	cp $(DELETE_DIR)/NANOV200-delete-emulator-write-test.IMG $(OUT)
endif
	$(FWTOOL) crc $(OUT)
	@printf '\nBuilt firmware %s (date %s): %s\n' '$(VERSION)' '$(DATE)' '$(OUT)'

# C code run from RAM during the library refresh; syms.inc exports its symbols to patches/*.S
ifeq ($(V2_PROFILE),0)
$(BLOB) out/gen/syms.inc &: $(SRC) src/hooks.txt tools/build_blob.sh tools/hooks.py
	tools/build_blob.sh out/gen $(FW)
endif

$(TETRIS_MODE_STAMP):
	@mkdir -p $(GEN_DIR)
	@rm -f $(GEN_DIR)/.tetris-mode-*
	@touch $@

$(GAME) $(GEN_DIR)/tetris_syms.inc &: $(GAME_SRC) tools/build_tetris.sh src/libc.c src/fw.h $(TETRIS_MODE_STAMP)
	bash tools/build_tetris.sh $(GEN_DIR) $(FW) $(TETRIS_DIAGNOSTIC) $(V2_PROFILE)

# stock code, new date: the loader boots the newest valid copy, so the original
# image would not replace a newer custom build
ifeq ($(origin DATE),file)
recovery: DATE := 20260826
endif
recovery: check
	@mkdir -p out/recovery
	$(FWTOOL) patch $(FW) out/recovery/NANOV170.IMG --date $(DATE)
	$(FWTOOL) crc out/recovery/NANOV170.IMG

ifeq ($(V2_PROFILE),0)
out/gen/%.patch: patches/%.S patches/fw.inc out/gen/syms.inc out/gen/tetris_syms.inc tools/asm2patch.sh
	tools/asm2patch.sh $< $@
else
$(GEN_DIR)/%.patch: patches/v2/%.S patches/v2/fw.inc $(GEN_DIR)/tetris_syms.inc tools/asm2patch.sh
	tools/asm2patch.sh $< $@
endif

verify:
	$(FWTOOL) crc $(OUT)

info:
	$(FWTOOL) info $(FW)

# byte ranges that differ from stock
diff:
	@cmp -l $(FW) $(OUT) | awk 'NR==1||$$1!=p+1{if(NR>1)printf "-0x%x\n",p-1;printf "0x%x",$$1-1}{p=$$1}END{if(NR)printf "-0x%x\n",p-1}'

clean:
	rm -rf out
