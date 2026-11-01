"""Explicit airport-associated departure VORs verified against AIP AD 2.19.

Includes off-airport stations per #244. Never infer association from proximity.
See docs/evidence/airport-vor-departure.json for primary-source provenance.
"""

from .planning import DepartureVorSelection

AIRPORT_VORS: dict[str, DepartureVorSelection] = {
    "RJFC": DepartureVorSelection(
        airport_id="RJFC",
        identifier="YKE",
        latitude_deg=30.379447222222222,
        longitude_deg=130.66271666666668,
        source="AIP RJFC AD 2.19; RJFC__20251030.pdf p.7",
        source_revision="5c9a507f1b6a1435fbf7672900aa7adb4fe54ca7e3587e740b247d86e1917ae4",
    ),
    "RJFE": DepartureVorSelection(
        airport_id="RJFE",
        identifier="FUE",
        latitude_deg=32.66860555555555,
        longitude_deg=128.8267361111111,
        source="AIP RJFE AD 2.19; RJFE__20250220.pdf p.6",
        source_revision="b89a4a4cb5f787ccb4a45d42282ea55ee29e933b004421d034630957f68b7ad6",
    ),
    "RJFG": DepartureVorSelection(
        airport_id="RJFG",
        identifier="TGE",
        latitude_deg=30.60215555555556,
        longitude_deg=130.9915333333333,
        source="AIP RJFG AD 2.19; RJFG__20241128.pdf p.7",
        source_revision="654936b205cf31290ccbbada2c71fe45479bfe50855fe9c5bf847c6ee506c10b",
    ),
    "RJFK": DepartureVorSelection(
        airport_id="RJFK",
        identifier="KGE",
        latitude_deg=31.797541666666667,
        longitude_deg=130.72610277777778,
        source="AIP RJFK AD 2.19; RJFK__20260806.pdf p.13",
        source_revision="ab15885a5d4eb533ba89dc14dfa7a236edad9c10656fa241103a87d0e546e0af",
    ),
    "RJFM": DepartureVorSelection(
        airport_id="RJFM",
        identifier="MZE",
        latitude_deg=31.87872777777778,
        longitude_deg=131.43746666666667,
        source="AIP RJFM AD 2.19; RJFM__20260301.pdf p.9",
        source_revision="f0c586193c699bf208b5ed1b3476e3b01fd4c134236a0bf6aaef2134fcedfc9c",
    ),
    "RJFO": DepartureVorSelection(
        airport_id="RJFO",
        identifier="TFE",
        latitude_deg=33.48971388888889,
        longitude_deg=131.72875555555555,
        source="AIP RJFO AD 2.19; RJFO__20261001.pdf p.9",
        source_revision="ec8bfda481e6ed33399e2119bec7e9053e1eccec77024c5701fc5ceaa81f4391",
    ),
    "RJFS": DepartureVorSelection(
        airport_id="RJFS",
        identifier="SGE",
        latitude_deg=33.14861944444444,
        longitude_deg=130.29289722222222,
        source="AIP RJFS AD 2.19; RJFS__20260611.pdf p.9",
        source_revision="bfbd8b7075b323b98e4218e30c3393d2cee1e4362a9eb5f86934ad5fbd6c0a5e",
    ),
    "RJFT": DepartureVorSelection(
        airport_id="RJFT",
        identifier="KUE",
        latitude_deg=32.83480277777778,
        longitude_deg=130.8415138888889,
        source="AIP RJFT AD 2.19; RJFT__20261001.pdf p.11",
        source_revision="be16dd707cc661400f581ca66be99abc01426ebfec6493312fb0169cce7d6674",
    ),
    "RJFU": DepartureVorSelection(
        airport_id="RJFU",
        identifier="OLE",
        latitude_deg=32.90524722222222,
        longitude_deg=129.91798055555554,
        source="AIP RJFU AD 2.19; RJFU__20261001.pdf p.11",
        source_revision="d5758f59fee86a399ccc9f5b21076816348215ab8c1d094cad9d1164e51eed72",
    ),
    "RJOA": DepartureVorSelection(
        airport_id="RJOA",
        identifier="HGE",
        latitude_deg=34.433775,
        longitude_deg=132.92396944444442,
        source="AIP RJOA AD 2.19; RJOA__20260903.pdf p.10",
        source_revision="d6c8496c46b8ab739631297d30837bfa2ab129117fdab4d58ae72b921488cb9e",
    ),
    "RJOB": DepartureVorSelection(
        airport_id="RJOB",
        identifier="OYE",
        latitude_deg=34.75038333333333,
        longitude_deg=133.83505555555556,
        source="AIP RJOB AD 2.19; RJOB__20261001.pdf p.7",
        source_revision="4a6e9f7b41d34f84e7a185f17d6647c204c48d0f622d09b2f1b4c38af0124cee",
    ),
    "RJOK": DepartureVorSelection(
        airport_id="RJOK",
        identifier="NAE",
        latitude_deg=33.55090833333333,
        longitude_deg=133.65911111111112,
        source="AIP RJOK AD 2.19; RJOK__20260806.pdf p.8",
        source_revision="d3c4b1b42daa751e02d141649aa98a36b034b2e414bbf3ef59a6b55c499be91e",
    ),
    "RJOM": DepartureVorSelection(
        airport_id="RJOM",
        identifier="MYE",
        latitude_deg=33.83010277777778,
        longitude_deg=132.69222222222223,
        source="AIP RJOM AD 2.19; RJOM__20261001.pdf p.9",
        source_revision="a13190a598fcd6513a7e588bab3731a1467ae7f594e3b3cd47cc07d6437e9c44",
    ),
    "RJOT": DepartureVorSelection(
        airport_id="RJOT",
        identifier="KTE",
        latitude_deg=34.212497222222225,
        longitude_deg=134.02259166666667,
        source="AIP RJOT AD 2.19; RJOT__20260806.pdf p.10",
        source_revision="53ab325e626ab218d7a0ec0d032f5230cd271864e97bd092104f5df6e65c9bfc",
    ),
}
