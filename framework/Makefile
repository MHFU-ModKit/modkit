# build/mhfu_framework.prx and build/mhfu_boot.prx: `make -C framework`. Needs docker, uv and make;
# the image has no Python, so the address tables are rendered here first.

IMAGE = pspdev/pspdev:v20260901@sha256:b22811072ea0721d7f6c666a5a279b6def2b0cd95892b819d9570f8ffc4452c0
GEN   = build/gen
ADDR  = ../packages/mhfu/src/mhfu/addresses
PRXS  = build/mhfu_framework.prx build/mhfu_boot.prx

.PHONY: all clean

# psp-prxgen copies EF_MIPS_ABI_EABI32 into the PRX, which old firmware rejects (0x800200D9)
all: $(GEN)/addresses.gen.h $(GEN)/addresses.lua
	docker run --rm --platform linux/amd64 --user "$$(id -u):$$(id -g)" -v "$(CURDIR)":/work -w /work \
	    $(IMAGE) make -f Makefile.psp $(if $(findstring k,$(firstword -$(MAKEFLAGS))),-k)
	uv run python tools/patch_prx_eflags.py $(PRXS)

$(GEN)/addresses.gen.h: $(ADDR).toml $(ADDR).py
	@mkdir -p $(@D)
	uv run python -m mhfu.addresses c -o $@

$(GEN)/addresses.gen.lua: $(ADDR).toml $(ADDR).py
	@mkdir -p $(@D)
	uv run python -m mhfu.addresses lua -o $@

# the copy the PRX embeds, without the doc comments (a third of the size)
$(GEN)/addresses.lua: $(GEN)/addresses.gen.lua
	grep -v '^[[:space:]]*---' $< > $@

clean:
	rm -rf build
