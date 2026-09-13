"""Golden readings of the 16 dataset images, transcribed by a human, for EVALUATING the vision reader.

These values are never used by the runtime (see code/evidence/images.py); they exist so that the
reader's accuracy can be measured (tests/test_agents.py::test_vision_reader_against_golden_set) and
regressions in prompts or arbitration are caught. Each row is pinned to the sha256 of the PNG it was
read from so the evaluation is void if the file changes.

image_id -> (amount, currency, field, note, sha256)
"""

GOLDEN = {
    "image_01": ("4365000", "IDR", "Net Pay", "payslip Aug-2019 net pay", "f37b40e6af42c664846057252cac89ad41b7d029dfe8dacff2db8cceb79fa5ba"),
    "image_02": ("100000", "INR", "Balance Due", "rent receipt: 2,00,000 total, 1,00,000 received, balance due 1,00,000", "ccd779e5382b1bcfacfb47d4ccf346ffd667a34c8b94d0cfd48aa4a609bd117d"),
    "image_03": ("41272", "INR", "Net Amount", "grocery bill of supply", "e5fb0bbcda6cc06f8ea95e32e45d4c76acd8c594f4b02ff0d78e6006e103ee4d"),
    "image_04": ("2854", "INR", "Item Bill", "cropped delivery order; delivery fee cut off", "281e7f1e7bd1f98fbd53cde1381977373e610e6634b11c98098000001ff10f0c"),
    "image_05": ("704.05", "INR", "Amount due till 06-Feb-2026", "telecom bill; 822.05 applies after the due date", "9abcda5647afb3dcdf91613253ac0160bd722333af33a4b952dfc96fea6ff97b"),
    "image_06": ("1995", "INR", "Total", "Blinkit tax invoice", "9055551fbe5940feb01b947e1f18ccfed093192d103b1e930a56df0ea7cd3cb4"),
    "image_07": ("8528", "INR", "Grand Total", "restaurant tax invoice (sub total 8122 + taxes)", "f6d30a74355224c0b5cda2d7f96399a7b1a0afe4f9fe59ea048bbecb1a21311e"),
    "image_08": ("15339", "INR", "Total Amount Received", "property maintenance receipt", "e28592ad8b4dacd03055e0b1ebc46670c83fbfa1162af07bdef33bb226bf63c8"),
    "image_09": ("723", "INR", "Total Amount Received", "water bill receipt", "e0e74e14425d923ff8a5c6db26ec6f4f26ee4697bfd257e414ba05c947a75ba8"),
    "image_10": ("79679.26", "INR", "Balance Due / Total", "grocery invoice", "c90f98caf0877083e471fd47dace772f83d4782037cf112e97c63f79a10ea8cf"),
    "image_11": ("3650", "INR", "Balance", "hospital provisional bill", "795e000d48428c97748e8af370cb02b604bec88cc52ec8930f38dc744624e886"),
    "image_12": ("33.50", "USD", "Total", "taxi receipt (cash paid 40, change 6.50)", "e10b0123e66d512d82f6c431fb741053b336627b89b9d9a138071ac6980a14ff"),
    "image_13": ("2298", "INR", "Total paid", "tote bag order", "1ae54b378a9556d94b753093ba80e7117caf86fab4d3fe11ec84e3dd2f6d6dd8"),
    "image_14": ("4543", "INR", "TOTAL", "handwritten pharmacy bill; items 1500+724+796+550+303+670 = 4543 (the 4 is easily read as 9)", "bf88e4aa35e6f36304cbf76bf6f505f32b04466bfd5f7df693fcb3ebe8a3e2c1"),
    "image_15": ("9968", "INR", "Grand Total", "flight tax invoice", "0c0fe3d79e670f2b423bbb2aafc0b5601d3eb4e659ac64058cd189abf7792ee1"),
    "image_16": ("393.22", "INR", "Total", "EV charging invoice", "2665cf731a861ddb217be5b8082fbd390850a7a98feec018519b6fda0b30b4f8"),
}
