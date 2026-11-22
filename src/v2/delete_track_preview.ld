ENTRY(delete_preview_get_key)
SECTIONS
{
    . = 0x0307d9d8;
    .payload :
    {
        *(.text*)
        *(.rodata*)
        *(.data*)
        . = ALIGN(4);
        *(.bss*)
        . = ALIGN(4);
    }
    ASSERT(. <= 0x0307e724, "Preview overlaps the V2 decoding overlays")
    .successor 0x0309f000 :
    {
        *(.successor)
        *(.successor_refresh)
        *(.successor_browser)
        *(.successor_data)
    }
    ASSERT(. <= 0x0309ff80, "Successor code overlaps the playback-open wrapper")
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) *(.note*) }
}
