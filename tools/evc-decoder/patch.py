"""Apply the narrowly scoped EVC decoder patch to pristine FFmpeg 4.2.9."""
from pathlib import Path
import sys

root = Path(sys.argv[1]) / "libavcodec"


def replace(name, before, after):
    path = root / name
    text = path.read_text()
    if after in text:
        return
    if text.count(before) != 1:
        raise RuntimeError(f"Unexpected FFmpeg source: {name}")
    path.write_text(text.replace(before, after))


replace("cabac.h", "    PutBitContext pb;", "    PutBitContext pb;\n    uint8_t *evc_states;\n    int evc_key;")
replace("h264dec.h", "    int is_avc;", "    int is_avc;\n    int is_evc;")
replace("h264dec.c", "static const AVOption h264_options[] = {", '''static const AVOption h264_options[] = {
    { "is_evc", "EV CABAC context key (0 = standard)", OFFSET(is_evc), AV_OPT_TYPE_INT, {.i64 = 0}, 0, 512, VD },''')
replace("h264_cabac.c", "/* Cabac pre state table */", '#include "evc_context.h"\n\n/* Cabac pre state table */')
replace("h264_cabac.c", "    /* calculate pre-state */", "    sl->cabac.evc_states = sl->cabac_state;\n    sl->cabac.evc_key = h->is_evc;\n\n    /* calculate pre-state */")
replace("h264_cabac.c", "    CABACContext cc;", "    CABACContext cc;\n    cc.evc_states = sl->cabac.evc_states;\n    cc.evc_key = sl->cabac.evc_key;")
# Thread contexts receive the user option when their initial state is copied.
replace("h264_slice.c", "    h->is_avc = h1->is_avc;", "    h->is_avc = h1->is_avc;\n    h->is_evc = h1->is_evc;")
(root / "evc_context.h").write_bytes(Path(__file__).with_name("evc_context.h").read_bytes())
