"""
Property-specific unit mapping functions.

66 MAP_* functions that translate GL memo unit strings (e.g. "4601F@203")
into Entrata building IDs and unit IDs. Each function encodes the unique
addressing scheme used at a specific property.

DO NOT REFACTOR these functions. They are proven production logic extracted
verbatim from the original VE script.
"""
import re


# ============ HELPER FUNCTIONS ============

def UNITSTRING(description):
    """Extract unit string pattern like '4601F@203' from a GL memo description."""
    try:
        result = re.search(r"\d+[a-zA-Z]*@\S*", description).group()
        result = result.rstrip(')')
        result = result.rstrip('(')
        return result
    except:
        return None


def ST_NUM(unit_string):
    """Street number portion (digits before the letter+@ marker)."""
    at_sign_index = unit_string.index("@")
    return unit_string[:at_sign_index - 1]


def ST_LETTER(unit_string):
    """Single letter immediately before the @ sign."""
    at_sign_index = unit_string.index("@")
    return unit_string[at_sign_index - 1]


def APT_NUM(unit_string):
    """Apartment/unit portion after the @ sign."""
    at_sign_index = unit_string.index("@")
    return unit_string[at_sign_index + 1:]


def ADD_LEAD_ZEROES(a_string, wanted_length):
    """Pad string with leading zeroes to desired length."""
    n = wanted_length - len(a_string)
    if n > 0:
        return ("0" * n) + a_string
    else:
        return a_string


def ISINARRAY(string, array):
    return string in array


def GETNUMERIC(string):
    """Extract only digit characters from a string."""
    result = ""
    for ch in string:
        if ch.isdigit():
            result += ch
    return result


def ISALPHA(string):
    return string.isalpha()


def GETALPHA(string):
    """Extract only alphabetic characters from a string."""
    return re.sub(r'[0-9]', '', string)


# ============ PROPERTY MAPPING FUNCTIONS ============

CKN_SUFFIX_MAP = {
    "4828S": "1A", "4820S": "2B", "13401R": "3C",
    "4824S": "4D", "4827F": "5E", "4829F": "6F",
}


def MAP_WOO(unit_string):
    return ["01", ST_NUM(unit_string) + APT_NUM(unit_string)]


def MAP_SMT(unit_string):
    num = str(ST_NUM(unit_string)).zfill(4)
    return ["01", num]


def MAP_MDL(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_CKN(unit_string):
    street_num = ST_NUM(unit_string)
    street_letter = ST_LETTER(unit_string)
    base = street_num + street_letter
    suffix = CKN_SUFFIX_MAP.get(base)
    if suffix is None:
        return [None, None]
    apt = APT_NUM(unit_string)
    prefix = ADD_LEAD_ZEROES(apt, 4)
    unit_id = prefix + "-" + suffix
    return [None, unit_id]


def MAP_LUM(unit_string):
    return [None, APT_NUM(unit_string)]


def MAP_DLX(unit_string):
    return [None, APT_NUM(unit_string)]


def MAP_WES(unit_string):
    return [None, "550" + APT_NUM(unit_string)]


def MAP_FRE(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_9WT(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_ADP(unit_string):
    return ["01", ST_NUM(unit_string)]


def MAP_APX(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_HUD(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_ARO(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"9841": "01", "9829": "02", "9818": "03", "9806": "04", "9805": "05",
                  "9793": "06", "9781": "07", "9769": "08", "9757": "09", "9745": "10",
                  "9733": "11", "9721": "12", "9709": "13", "9697": "14", "9698": "15",
                  "9715": "16", "9691": "17", "9703": "18"}
    prefix = prefix_map.get(street_num, '')
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_ACO(unit_string):
    unit_code = APT_NUM(unit_string)
    unit_code = unit_code[1:] + unit_code[0]
    return ["01", unit_code]


def MAP_ARW(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"421": "01", "415": "02", "327": "03", "1820": "04", "315": "05",
                  "312": "06", "309": "07", "1911": "08", "225": "09", "1817": "10",
                  "215": "11", "1809": "12"}
    prefix = prefix_map.get(street_num, '')
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_BMS(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_BOJ(unit_string):
    st_num = ST_NUM(unit_string)
    bldg_map = {"391": "01", "381": "02", "371": "03", "411": "04", "421": "05",
                "431": "06", "441": "07", "451": "08"}
    bldg_id = bldg_map.get(st_num)
    apt = APT_NUM(unit_string)
    return [bldg_id, str(int(bldg_id)) + apt]


def MAP_CBW(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_CAB(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"142": "AMH", "134": "AND", "138": "CON", "130": "EXE",
                  "140": "HAR", "132": "MER", "136": "MIL"}
    prefix = prefix_map.get(street_num, '')
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_CAC(unit_string):
    return ["01", ST_NUM(unit_string) + APT_NUM(unit_string)]


def MAP_CCL(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_WST(unit_string):
    street_num = ST_NUM(unit_string)
    bldg_map = {"2315": "01", "2220": "02", "2602": "03"}
    bldg_id = bldg_map.get(street_num)
    unit_id = APT_NUM(unit_string)
    if len(unit_id) == 2:
        unit_id = unit_id[0] + "0" + unit_id[1:]
    return [bldg_id, unit_id]


def MAP_CRO(unit_string):
    street_num = ST_NUM(unit_string)
    return ["01", street_num[len(street_num) - 2:] + APT_NUM(unit_string)]


def MAP_CCD(unit_string):
    street_num = ST_NUM(unit_string)
    return ["01", street_num[len(street_num) - 2:] + APT_NUM(unit_string)]


def MAP_CDP(unit_string):
    street_num = ST_NUM(unit_string)
    return [street_num[2:], APT_NUM(unit_string)]


def MAP_CEH(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"100": "A", "200": "B", "300": "C", "400": "D", "500": "E"}
    prefix = prefix_map.get(street_num, "")
    suffix = APT_NUM(unit_string)
    if suffix[0:2] == "PH":
        suffix = "6" + ADD_LEAD_ZEROES(GETNUMERIC(suffix), 2)
    else:
        suffix = ADD_LEAD_ZEROES(GETNUMERIC(suffix), 3)
    return ["01", prefix + suffix]


def MAP_CPK(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 5)]


def MAP_CHA(unit_string):
    street_num = ST_NUM(unit_string)
    street_letter = ST_LETTER(unit_string)
    if street_letter == "K":
        prefix = "KC"
    elif street_letter == "L":
        prefix = "LT"
    elif street_letter == "B":
        prefix = "BT"
    elif street_letter == 'C':
        if len(street_num) == 4:
            prefix = "CA"
        elif len(street_num) == 3:
            prefix = "CT"
    return ["01", prefix + street_num]


def MAP_CBA(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_WIN(unit_string):
    street_num = ST_NUM(unit_string)
    bldg_map = {"11572": "01", "11300": "02", "11312": "03", "11324": "04", "11349": "05",
                "11403": "06", "11415": "07", "11427": "08", "11439": "09", "11440": "10",
                "11452": "11", "11464": "12", "11500": "13", "11512": "14", "11524": "15",
                "11536": "16", "11548": "17", "11560": "18"}
    bldg_id = bldg_map.get(street_num, "")
    return [bldg_id, bldg_id + APT_NUM(unit_string)]


def MAP_CLO(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"801": "1", "810": "2", "820": "3", "840": "4", "850": "5",
                  "860": "6", "870": "7", "880": "8", "890": "9"}
    prefix = prefix_map.get(street_num, '')
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_CON(unit_string):
    unit_id = APT_NUM(unit_string)
    unit_id = unit_id.replace("-", "")
    return ["01", unit_id]


def MAP_CPA(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)]


def MAP_DRF(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_DIA(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 3)]


def MAP_DOH(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 3)]


def MAP_ESS(unit_string):
    return ["01", ADD_LEAD_ZEROES(ST_NUM(unit_string), 4)]


def MAP_GRA(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"2950": "01", "2940": "02", "2990": "03", "2970": "04", "3055": "05",
                  "2965": "06", "2975": "07", "2985": "08", "665": "09", "675": "10"}
    prefix = prefix_map.get(street_num, '')
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_GER(unit_string):
    return ["01", ST_NUM(unit_string) + ST_LETTER(unit_string)]


def MAP_HAR(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_HAM(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)]


def MAP_FLD(unit_string):
    street_num = ST_NUM(unit_string)
    street_abbr = ST_LETTER(unit_string)
    if street_abbr == "D":
        multis = ["12367", "12369", "12375", "12377", "12383", "12385", "12405", "12407", "12415", "12417", "12423", "12425"]
        if ISINARRAY(street_num, multis):
            unit_id = "D" + street_num[len(street_num) - 3:] + APT_NUM(unit_string)
        else:
            unit_id = "D" + street_num[len(street_num) - 3:] + "000"
    elif street_abbr == "L":
        multis = ["12410", "12412", "12420", "12456", "12462", "12468", "12471", "12473", "12474", "12482", "12483", "12484", "12485", "12491", "12492", "12493", "12494", "12499"]
        if ISINARRAY(street_num, multis):
            unit_id = "L" + street_num[len(street_num) - 3:] + APT_NUM(unit_string)
        else:
            unit_id = "L" + street_num[len(street_num) - 3:] + "000"
    elif street_abbr == "M":
        multis = ["12465", "12467", "12477", "12479"]
        if ISINARRAY(street_num, multis):
            unit_id = "M" + street_num[len(street_num) - 3:] + APT_NUM(unit_string)
        else:
            unit_id = "M" + street_num[len(street_num) - 3:] + "000"
    elif street_abbr == "O":
        multis = ["1908", "1910", "1984"]
        if ISINARRAY(street_num, multis):
            unit_id = "O" + street_num[len(street_num) - 3:] + APT_NUM(unit_string)
        else:
            unit_id = "O" + street_num[len(street_num) - 3:] + "000"
    elif street_abbr == "A":
        unit_id = "A" + street_num[len(street_num) - 3:] + "000"
    else:
        unit_id = ''
    return ["01", unit_id]


def MAP_HVC(unit_string):
    unit_id = APT_NUM(unit_string)
    bldg_id = ""
    if len(unit_id) == 3:
        bldg_id = "0" + unit_id[0]
    elif len(unit_id) == 4:
        bldg_id = unit_id[0:2]
    return [bldg_id, unit_id]


def MAP_HFH(unit_string):
    street_num = ST_NUM(unit_string)
    street_abbr = ST_LETTER(unit_string)
    unit_code = GETNUMERIC(APT_NUM(unit_string))
    unit_id = ""
    if street_num == "200" and street_abbr == "C":
        a_units = ["101", "102", "103", "104", "107", "114", "119", "206", "207", "213", "306", "307", "313", "411", "413"]
        ada_units = ["106", "212"]
        u_units = ["111"]
        if ISINARRAY(unit_code, a_units):
            unit_id = "G" + unit_code + "A"
        elif ISINARRAY(unit_code, ada_units):
            unit_id = "G" + unit_code + "ADA"
        elif ISINARRAY(unit_code, u_units):
            unit_id = "G" + unit_code + "U"
        else:
            unit_id = "G" + unit_code
    elif street_num == "201" and ISINARRAY(street_abbr, ["T", "W"]):
        a_units = ["101", "102", "107", "112", "117", "118", "119", "207", "218", "219", "307", "318", "319"]
        if ISINARRAY(unit_code, a_units):
            unit_id = "M" + unit_code + "A"
        else:
            unit_id = "M" + unit_code
    elif street_num == "201" and street_abbr == "C":
        a_units = ["101", "102", "107", "120", "121", "206", "207", "306", "307"]
        ada_units = ["106", "212"]
        if ISINARRAY(unit_code, a_units):
            unit_id = "T" + unit_code + "A"
        elif ISINARRAY(unit_code, ada_units):
            unit_id = "T" + unit_code + "ADA"
        else:
            unit_id = "T" + unit_code
    return ["01", unit_id]


def MAP_HEL(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)]


def MAP_CHC(unit_string):
    street_num = ST_NUM(unit_string)
    prefix = ""
    if street_num == "600" and ST_LETTER(unit_string) == "C":
        prefix = "17"
    elif len(street_num) == 3:
        prefix = "0" + street_num[0]
    elif len(street_num) == 4:
        prefix = street_num[0:2]
    return ["01", prefix + "-" + APT_NUM(unit_string)]


def MAP_CAR(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"2420": "01", "1941": "02", "2330": "03", "2301": "04", "2215": "05",
                  "2111": "06", "2530": "07", "2518": "08", "1735": "09", "1707": "10", "1506": "11"}
    prefix = prefix_map.get(street_num, "")
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_ING(unit_string):
    unit_code = APT_NUM(unit_string)
    if len(unit_code) > 0:
        unit_id = ADD_LEAD_ZEROES(unit_code, 4)
    else:
        unit_id = ST_NUM(unit_string)
    return ["01", unit_id]


def MAP_IND(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 5)]


def MAP_INC(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_HAL(unit_string):
    a = ADD_LEAD_ZEROES(APT_NUM(unit_string), 3)
    if len(APT_NUM(unit_string)) < 3:
        a = "T" + a
    else:
        a = "P" + a
    return ["01", a]


def MAP_JEF(unit_string):
    return ["01", ST_NUM(unit_string)]


def MAP_ML1(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_ML2(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_MON(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 3)]


def MAP_ONE(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_PPN(unit_string):
    unit_code = APT_NUM(unit_string)
    if len(unit_code) > 0:
        unit_id = ADD_LEAD_ZEROES(unit_code, 4)
    else:
        unit_id = ST_NUM(unit_string)
    return ["01", unit_id]


def MAP_PAG(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_RAN(unit_string):
    unit_code = APT_NUM(unit_string)
    if len(unit_code) == 2:
        unit_code = unit_code[0] + "0" + unit_code[-1]
    return ["01", unit_code]


def MAP_TNG(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"2170": "01", "2160": "02", "2150": "03", "2140": "04", "2120": "05",
                  "2130": "06", "2110": "07", "2165": "08", "2145": "09", "2135": "10",
                  "2115": "11", "2125": "12", "2155": "13", "2175": "14"}
    if street_num in prefix_map:
        prefix = prefix_map[street_num]
    elif street_num in ["555", "547", "553", "551", "545", "543", "541", "539", "549"]:
        prefix = "15"
    elif street_num in ['557', '559', '561', '563', '565']:
        prefix = "16"
    elif street_num in ["431", "429", "427", "425"]:
        prefix = "17"
    elif street_num in ["413", "415", "417", "419"]:
        prefix = "18"
    elif street_num in ['333', '331', '329', '327', '325']:
        prefix = '19'
    elif street_num in ['215', '217', '219', '221']:
        prefix = '20'
    elif street_num in ['315', '317', '319', '321']:
        prefix = '21'
    else:
        prefix = ''
    return ['01', prefix + APT_NUM(unit_string)]


def MAP_TEW(unit_string):
    street_abbr = ST_LETTER(unit_string)
    if street_abbr == "A":
        unit_id = "G" + ST_NUM(unit_string)
    elif street_abbr == "O":
        unit_id = "T" + ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)
    else:
        unit_id = ''
    return ["01", unit_id]


def MAP_NOR(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"8100": "0", "8102": "2", "8104": "4", "8106": "6", "8108": "8",
                  "8110": "10", "8112": "12", "8116": "16", "8118": "18", "8120": "20",
                  "8122": "22", "8124": "24", "8126": "26", "8128": "28"}
    prefix = prefix_map.get(street_num, '')
    return [street_num[2:], prefix + ADD_LEAD_ZEROES(APT_NUM(unit_string), 2)]


def MAP_RCE(unit_string):
    prefix = ADD_LEAD_ZEROES(ST_NUM(unit_string), 2)
    suffix = ADD_LEAD_ZEROES(APT_NUM(unit_string), 2)
    return ["01", prefix + '-' + suffix]


def MAP_RBV(unit_string):
    return ['01', 'Fixme']


def MAP_LBC(unit_string):
    unit_num = APT_NUM(unit_string)
    return ["01", ADD_LEAD_ZEROES(unit_num, 3)]


def MAP_SFR(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"1413": "01", "1411": "02", "1407": "03", "1409": "04", "1410": "11",
                  "1408": "12", "1406": "13", "1405": "14", "1404": "15", "1402": "16",
                  "1400": "17", "1403": "18"}
    prefix = prefix_map.get(street_num, '')
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_SRT(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"5170": "1", "5150": "2", "5130": "3", "5094": "4", "5090": "5",
                  "5070": "6", "5050": "7", "5030": "8"}
    prefix = prefix_map.get(street_num, '')
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_SRP(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"702": "A", "708": "B", "712": "C", "718": "D", "722": "E", "728": "F",
                  "732": "G", "742": "H", "748": "I", "752": "J", "758": "K", "762": "L",
                  "768": "M", "772": "N"}
    prefix = prefix_map.get(street_num, '')
    suffix = GETNUMERIC(APT_NUM(unit_string))
    return ["01", prefix + suffix]


def MAP_SOL(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)]


def MAP_SVR(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"1500": "1", "1501": "2", "1520": "3", "1540": "4", "1414": "5", "1412": "6"}
    prefix = prefix_map.get(street_num, '')
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_SSG(unit_string):
    return None


def MAP_SPY(unit_string):
    return ['01', APT_NUM(unit_string)]


def MAP_SMB(unit_string):
    return ['01', ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)]


def MAP_TM1(unit_string):
    return ['01', APT_NUM(unit_string)]


def MAP_TM2(unit_string):
    return ['01', APT_NUM(unit_string)]


def MAP_TGH(unit_string):
    return ['01', APT_NUM(unit_string)]


def MAP_6TH(unit_string):
    prefix = ST_NUM(unit_string)
    if prefix == "274":
        prefix = "74"
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_CPW(unit_string):
    return ['01', ADD_LEAD_ZEROES(APT_NUM(unit_string), 5)]


def MAP_EST(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_CNR(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"1435": "1", "1425": "2"}
    prefix = prefix_map.get(street_num, '')
    return ["01", prefix + "-" + ADD_LEAD_ZEROES(APT_NUM(unit_string), 3)]


def MAP_PNC(unit_string):
    prefix = ST_NUM(unit_string)
    prefix = prefix[1:]
    return ["01", prefix + APT_NUM(unit_string)]


def MAP_SVP(unit_string):
    return ["01", ADD_LEAD_ZEROES(ST_NUM(unit_string), 4)]


def MAP_CDS(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)]


def MAP_ENC(unit_string):
    return ["01", ADD_LEAD_ZEROES(ST_NUM(unit_string), 4)]


def MAP_EPS(unit_string):
    return ['01', ADD_LEAD_ZEROES(ST_NUM(unit_string), 4) + APT_NUM(unit_string)]


def MAP_TRL(unit_string):
    street_num = ST_NUM(unit_string)
    prefix_map = {"12023": "A", "12101": "B", "12022": "C", "12010": "D", "12232": "E",
                  "12224": "F", "12113": "G", "12117": "H"}
    prefix = prefix_map.get(street_num, '')
    return ["00", prefix + GETNUMERIC(APT_NUM(unit_string))]


def MAP_VEL(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 3)]


def MAP_VUE(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)]


def MAP_W18(unit_string):
    return ["01", APT_NUM(unit_string)]


def MAP_TDS(unit_string):
    return ["01", ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)]


def MAP_DUO(unit_string):
    return ["01", ST_NUM(unit_string) + APT_NUM(unit_string)]


def MAP_RPP(unit_string):
    apt = APT_NUM(unit_string)
    if 'W' in apt or 'E' in apt:
        fix_apt = apt[-1] + apt[0:len(apt) - 1]
    else:
        fix_apt = ADD_LEAD_ZEROES(apt, 4)
    return ['01', fix_apt]


def MAP_WCL(unit_string):
    return ['01', ADD_LEAD_ZEROES(APT_NUM(unit_string), 4)]


def MAP_EWC(unit_string):
    EWC_LOOKUP = {
        '101F': '01', '100S': '02', '101C': '03', '100R': '04', '101W': '05',
        '100W': '06', '103F': '07', '100F': '08', '103C': '09', '102R': '10',
        '103W': '11', '102W': '12', '102F': '13', '105C': '14', '104R': '15',
        '105W': '16', '104W': '17', '105F': '18', '104F': '19', '107C': '20',
        '205P': '21', '106R': '22', '107W': '23', '106W': '24', '200P': '25',
        '202P': '26', '206P': '27', '208P': '28', '201C': '29', '200F': '30',
        '201N': '31', '201S': '32', '300C': '33', '302C': '34', '300S': '35',
        '302S': '36', '304S': '37',
    }
    street_num = ST_NUM(unit_string)
    apt = APT_NUM(unit_string)
    if street_num and apt:
        street_part = unit_string.split('@')[0] if '@' in unit_string else unit_string
        letter_match = re.search(r'[A-Za-z]', street_part)
        if letter_match:
            street_code = street_num + letter_match.group(0).upper()
            bldg_prefix = EWC_LOOKUP.get(street_code)
            if bldg_prefix:
                return ['01', bldg_prefix + apt]
    return ['01', apt]


# ============ DISPATCH TABLE ============

MAPPING_DISPATCH = {
    "6TH": MAP_6TH, "9WT": MAP_9WT, "ACO": MAP_ACO, "ADP": MAP_ADP,
    "APX": MAP_APX, "ARO": MAP_ARO, "ARW": MAP_ARW, "BMS": MAP_BMS,
    "BOJ": MAP_BOJ, "CAB": MAP_CAB, "CAC": MAP_CAC, "CAR": MAP_CAR,
    "CBA": MAP_CBA, "CCW": MAP_CBW, "CCD": MAP_CCD, "CCL": MAP_CCL,
    "CDP": MAP_CDP, "CDS": MAP_CDS, "CEH": MAP_CEH, "CHA": MAP_CHA,
    "CHC": MAP_CHC, "CLO": MAP_CLO, "CNR": MAP_CNR, "CON": MAP_CON,
    "CPA": MAP_CPA, "CPK": MAP_CPK, "CPW": MAP_CPW, "CRO": MAP_CRO,
    "DIA": MAP_DIA, "DOH": MAP_DOH, "DRF": MAP_DRF, "DUO": MAP_DUO,
    "ENC": MAP_ENC, "EPS": MAP_EPS, "ESS": MAP_ESS, "EST": MAP_EST,
    "EWC": MAP_EWC, "FLD": MAP_FLD, "FRE": MAP_FRE, "GER": MAP_GER,
    "GRA": MAP_GRA, "HAL": MAP_HAL, "HAR": MAP_HAR, "HAM": MAP_HAM,
    "HEL": MAP_HEL, "HFH": MAP_HFH, "HUD": MAP_HUD, "HVC": MAP_HVC,
    "INC": MAP_INC, "IND": MAP_IND, "ING": MAP_ING, "JEF": MAP_JEF,
    "LBC": MAP_LBC, "ML1": MAP_ML1, "ML2": MAP_ML2, "MON": MAP_MON,
    "NOR": MAP_NOR, "ONE": MAP_ONE, "PAG": MAP_PAG, "PNC": MAP_PNC,
    "PPN": MAP_PPN, "RAN": MAP_RAN, "RBV": MAP_RBV, "RCE": MAP_RCE,
    "SFR": MAP_SFR, "RPP": MAP_RPP, "SMB": MAP_SMB, "SOL": MAP_SOL,
    "SPY": MAP_SPY, "SRP": MAP_SRP, "SRT": MAP_SRT, "SSG": MAP_SSG,
    "SVP": MAP_SVP, "SVR": MAP_SVR, "TDS": MAP_TDS, "TEW": MAP_TEW,
    "TGH": MAP_TGH, "TM1": MAP_TM1, "TM2": MAP_TM2, "TNG": MAP_TNG,
    "TRL": MAP_TRL, "VEL": MAP_VEL, "VUE": MAP_VUE, "W18": MAP_W18,
    "WCL": MAP_WCL, "WIN": MAP_WIN, "WOO": MAP_WOO, "WST": MAP_WST,
    "SMT": MAP_SMT, "LUM": MAP_LUM, "CKN": MAP_CKN, "DLX": MAP_DLX,
    "WES": MAP_WES, "MDL": MAP_MDL,
}


def MAP_UNIT(prop_code, unit_string):
    """Dispatch to the property-specific mapping function."""
    func = MAPPING_DISPATCH.get(prop_code)
    if func:
        return func(unit_string)
    return None


def BLDG(prop_code, unit_string):
    """Get building ID for a property + unit string."""
    result = MAP_UNIT(prop_code, unit_string)
    if result is None:
        return None
    return result[0]


def APT(prop_code, unit_string):
    """Get unit ID for a property + unit string."""
    result = MAP_UNIT(prop_code, unit_string)
    if result is None:
        return None
    return result[1]


# ============ RBV SPECIAL HANDLING ============

def IS_IN(string, description):
    """Check if string appears anywhere in description."""
    try:
        value = description.index(string)
    except:
        value = None
    return type(value) == int


def RBV(description):
    """
    Special mapping for RBV property. Uses full GL description (not unit string)
    because RBV's addressing scheme encodes street abbreviations in the memo text.
    """
    desc = description.strip().lstrip('(').rstrip(')')
    unit_match = re.search(r'V[EGWS]\s+(\S+)', desc)
    unit_part = unit_match.group(1) if unit_match else desc

    # Check multi-character abbreviations first (longest match wins)
    if IS_IN("WCW", unit_part) or IS_IN("WWC", unit_part):
        street_abbr = "WW"
    elif IS_IN("WCE", unit_part) or IS_IN("EWC", unit_part):
        street_abbr = "WE"
    elif IS_IN("WMC", unit_part) or IS_IN("MCW", unit_part):
        street_abbr = "MW"
    elif IS_IN("MCN", unit_part) or IS_IN("NMC", unit_part):
        street_abbr = "MN"
    elif IS_IN("MCE", unit_part) or IS_IN("EMC", unit_part):
        street_abbr = "ME"
    elif IS_IN("WS", unit_part):
        street_abbr = "WS"
    elif IS_IN("WR", unit_part):
        street_abbr = "WR"
    elif IS_IN("BR", unit_part):
        street_abbr = "B"
    elif IS_IN("PR", unit_part):
        street_abbr = "P"
    elif IS_IN("DC", unit_part):
        street_abbr = "D"
    elif IS_IN("CK", unit_part) or IS_IN("CKR", unit_part):
        street_abbr = "CK"
    elif IS_IN("CR", unit_part):
        street_abbr = "C"
    else:
        before_at = unit_part.split('@')[0] if '@' in unit_part else unit_part
        letters = GETALPHA(before_at).upper()
        single_letter_map = {'B': 'B', 'C': 'C', 'D': 'D', 'P': 'P', 'W': 'WR', 'E': 'WE', 'M': 'MW'}
        street_abbr = single_letter_map.get(letters, None)

    if street_abbr is None:
        return None

    house_num = GETNUMERIC(unit_part.split('@')[0] if '@' in unit_part else unit_part)
    if not house_num:
        return None
    return street_abbr + "-" + ADD_LEAD_ZEROES(house_num, 3)
