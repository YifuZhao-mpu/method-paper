"""Rule-based features for titles and abstracts (English)."""
import re

ABBR = r"(?<!\be\.g)(?<!\bi\.e)(?<!\bet al)(?<!\bvs)(?<!\bFig)(?<!\bEq)(?<!\bapprox)(?<!\bresp)(?<!\bcf)"
SENT_SPLIT = re.compile(ABBR + r"(?<=[.!?])\s+(?=[A-Z0-9(\"'“])")


def sentences(text):
    text = " ".join(text.split())
    parts = [p.strip() for p in SENT_SPLIT.split(text) if p.strip()]
    return parts


RX = lambda p: re.compile(p, re.I)  # noqa: E731
SELF = RX(r"\b(we|our|this (paper|work|study)|in this (paper|work)|here)\b")
PROPOSE = RX(r"\b(propose[sd]?|present(s|ed)?|introduce[sd]?|develop(s|ed)?|design(s|ed)?|devise[sd]?|formulate[sd]?|"
             r"contribute|put forward|offer|use|utili[sz]e|leverage|study|conduct|explore|investigate|tackle|address|"
             r"focus|aim|consider|seek|build|construct|learn|train|cast|frame|reformulate|extend|enable|adopt|employ|"
             r"exploit|observe|identify|argue|reveal|model|represent|decompose|derive|instead)\b")
EVAL = RX(r"\b(we|our (method|approach|model|framework|system|policy|policies))\s+(further\s+|also\s+|then\s+)?"
          r"(show|demonstrate|validate|evaluate|achieve|showcase|prove|verify|test|benchmark|attain|obtain|match|"
          r"outperform|surpass|reach)|\b(ablation|success rates?|benchmarks?|real[- ]world (experiments?|tasks?|"
          r"deployment)|on (\w+ ){0,3}(datasets?|tasks?|benchmarks?))\b|\bgenerali[sz]es? to\b|\bresults? (show|demonstrate)")
PROPOSE_STRONG = RX(r"\b(propose[sd]?|introduce[sd]?|present(s|ed)?|design(s|ed)?|devise[sd]?)\b")
TO_ADDRESS = RX(r"^(to (address|tackle|overcome|solve|mitigate|remedy|alleviate|bridge|fill|resolve|this end|achieve)|"
                r"in (response|contrast)|motivated by|inspired by|building (up)?on|based on (this|these|the) (observation|insight|analysis))")
METHOD_DETAIL = RX(r"^(specifically|concretely|in particular|first(ly)?|second(ly)?|then|next|finally|moreover|furthermore|"
                   r"additionally|besides|meanwhile|at its core|the (key|core) (idea|insight)|our (key|core) (idea|insight)|"
                   r"our (method|approach|framework|model|system|pipeline)|it\b|this (design|module|mechanism|formulation))")
RESULT = RX(r"\b((extensive|comprehensive|experimental|empirical|quantitative|qualitative)?\s*(experiments?|results?|evaluations?|"
            r"studies|analys[ie]s)\b.{0,160}\b(show|demonstrate|indicate|reveal|confirm|verify|validate|suggest)s?)|"
            r"\b(outperform|surpass|achiev|attain|reach)(s|es|ed|ing)?\b|state[- ]of[- ]the[- ]art|\bsota\b|"
            r"\bimprov(es|ed|ing)\b.{0,40}\d|\breduc(es|ed|ing)\b.{0,40}\d")
GAP = RX(r"\b(however|but|yet|despite|although|though|nevertheless|unfortunately|whereas|while existing|remain(s|ed)?|"
         r"still|limited|limitations?|fail(s|ed|ure)?|struggl(e|es|ed)|suffer(s|ed)?|challeng(e|es|ing)|bottleneck|"
         r"lack(s|ing)?|unclear|ignor(e|es|ed|ing)|overlook(s|ed)?|neglect(s|ed)?|costly|expensive|prohibitive|"
         r"inefficien(t|cy)|insufficient|inaccurate|degrad(e|es|ed|ation)|brittle|restrict(s|ed|ive)?|imped(e|es|ed)|"
         r"hinder(s|ed)?|ill-suited|inconsisten(t|cy)|complicat(e|es|ed)|need for|gaps?|noisy|difficult(y|ies)?|hard to|"
         r"problem(s|atic)?|issues?|drawbacks?|shortcomings?|sensitive to|prone to|vulnerab(le|ility)|unexplored|"
         r"under-?explored|open (problem|question)|non-trivial|ambigu(ous|ity)|unreliable|labou?r-intensive|tedious|"
         r"primarily|merely|rarely|cannot|unable)\b")
CONCL = RX(r"\b(code|github|project page|website|available at|will be (made )?(publicly )?(available|released)|"
           r"we hope|pave[sd]? the way|open(s)? (up )?(new|a) |shed[s]? light|future (work|research))\b|https?://")
CAUSE = RX(r"\b(because|due to|since|stem(s|ming)? from|caused by|aris(e|es|ing) from|owing to|as a result of|"
           r"lead(s|ing)? to|result(s|ing)? in)\b")
INSIGHT = RX(r"\b(key (insight|idea|observation)|core (insight|idea)|our (insight|idea|observation)|we (observe|find|show|"
             r"discover|identify|reveal|argue|hypothesi[sz]e) that|we (observe|identify|reveal|find)\b|"
             r"motivated by (the|this|our) (observation|insight)|insight|observation)\b")
NUMBER = RX(r"(\d+(\.\d+)?\s*(%|dB|×|x\b|times|fps|FPS|ms|hz|Hz|points?\b|pp\b|mAP|NDS|mIoU|PSNR))|"
            r"((by|of|to|from|up to|over|nearly|about|around)\s+\d+(\.\d+)?)|(\d+(\.\d+)?\s*(-|–)?\s*fold)")
SOTA = RX(r"state[- ]of[- ]the[- ]art|\bsota\b|outperform|surpass|superior to|beats?\b")
GENERAL = RX(r"plug[- ]and[- ]play|generali[sz](e|es|ed|ation|able)|agnostic|seamless(ly)?|"
             r"can be (easily |readily |directly )?(integrated|applied|plugged|incorporated|combined)|versatile|"
             r"general(-purpose)? framework|unified|any (existing|off-the-shelf)|off-the-shelf")
EFFIC = RX(r"real[- ]time|\bfaster\b|speed[- ]?up|\befficien(t|cy)\b|\bfps\b|memory|lightweight|\d+(\.\d+)?\s*(×|x)\b|"
           r"latency|compact")
HEDGE = RX(r"\b(may|might|could|potentially|possibly|somewhat|to some extent|appears? to|seems? to|preliminary|"
           r"partially|relatively)\b")
WEAK = RX(r"\b(slight(ly)?|marginal(ly)?|modest(ly)?|comparable|competitive|on par)\b")
CODE = RX(r"\b(code|github|project page|website)\b|https?://|will be (made )?(publicly )?(available|released)")
EXTENSIVE = RX(r"\b(extensive|comprehensive|thorough)\s+(experiments?|evaluations?|results|studies|analys[ie]s)")
FIRST = RX(r"\b(the first|first to|for the first time|first-ever|pioneer)")
REALWORLD = RX(r"real[- ]world|real robot|physical robot|real hardware|on hardware|in the real world|sim-to-real|sim2real")
NAMED = RX(r"\b(called|dubbed|named|termed|coined)\b")
ACRONYM_INTRO = re.compile(r"\(([A-Z][A-Za-z0-9\-]*[A-Z0-9][A-Za-z0-9\-]*)\)")
BENCH = RX(r"nuscenes|kitti|waymo|argoverse|scannet|replica|mip-?nerf\s*360|llff|tanks\s*(and|&)\s*temples|deep\s*blending|"
           r"dtu\b|blender|matterport|stanford2d3d|structured3d|360roam|libero|rlbench|calvin|meta-?world|simpler(env)?|"
           r"robomimic|maniskill|bridge(data)?|open x-embodiment|droid|semantickitti|view-of-delft|\bvod\b|k-radar|"
           r"imagenet|coco\b|objaverse|shapenet|modelnet|habitat|ai2-thor|robocasa|robotwin")
QUESTION = re.compile(r"\?")

TITLE_COLON_NAME = re.compile(r"^\s*([A-Z0-9][\w\-\+\.²³$^{}\\]*(\s+[A-Z0-9][\w\-\+\.]*){0,3})\s*:\s+\S")
TITLE_LEAD = RX(r"^(towards?|learning|rethinking|revisiting|beyond|unleashing|boosting|improving|efficient|real[- ]time|"
                r"generalizable|scaling|unified|on the|understanding|exploring|when|what|how|why|can|do|does|is|are)\b")
TYPE_DATA = RX(r"\b(dataset|benchmark|bench\b|survey|suite|testbed|leaderboard)\b")
TYPE_DATA_ABS = RX(r"\bwe (introduce|present|propose|release|collect|construct|build|curate)\s+(a|an|the)\s+"
                   r"([\w\-]+\s+){0,4}(dataset|benchmark|suite|testbed)")
TYPE_ANALYSIS = RX(r"^(rethinking|revisiting|understanding|an empirical|empirical|on the|do |does |are |is |what |how |why |"
                   r"when |a closer look|investigating|analy[sz]ing|demystifying|towards understanding)")


def classify_moves(sents):
    moves = []
    seen_m = False
    for s in sents:
        low = s.lower()
        if CONCL.search(s) and not RESULT.search(s):
            m = "C"
        elif RESULT.search(s) and (seen_m or NUMBER.search(s) or "experiment" in low or "result" in low):
            m = "R"
        elif seen_m and EVAL.search(s) and not PROPOSE_STRONG.search(s):
            m = "R"
        elif (SELF.search(s) and PROPOSE.search(s)) or TO_ADDRESS.search(s):
            m = "M"
        elif seen_m and (METHOD_DETAIL.search(s) or SELF.search(s)):
            m = "M"
        elif GAP.search(s) and not seen_m:
            m = "G"
        elif not seen_m:
            m = "B"
        else:
            m = "M"
        if m == "M":
            seen_m = True
        moves.append(m)
    return moves


def compress(moves):
    out = []
    for m in moves:
        if not out or out[-1] != m:
            out.append(m)
    return "".join(out)


def paper_type(title, abstract):
    if TYPE_DATA.search(title) or TYPE_DATA_ABS.search(abstract):
        return "dataset_benchmark"
    if TYPE_ANALYSIS.search(title) and not (SELF.search(abstract) and PROPOSE.search(abstract)):
        return "analysis"
    return "method"


def features(title, abstract):
    s = sentences(abstract)
    words = len(abstract.split())
    moves = classify_moves(s)
    comp = compress(moves)
    first_m = next((i for i, m in enumerate(moves) if m == "M"), None)
    gap_sents = [x for x, m in zip(s, moves) if m == "G"]
    res_sents = [x for x, m in zip(s, moves) if m == "R"]
    f = {
        "type": paper_type(title, abstract),
        "words": words,
        "n_sent": len(s),
        "words_per_sent": round(words / max(1, len(s)), 2),
        "moves": "".join(moves),
        "moves_c": comp,
        "first_move": moves[0] if moves else "",
        "first_M_pos": round(first_m / max(1, len(s) - 1), 3) if first_m is not None else "",
        "first_M_idx": first_m if first_m is not None else "",
        "has_B": int("B" in moves), "has_G": int("G" in moves), "has_M": int("M" in moves),
        "has_R": int("R" in moves), "has_C": int("C" in moves),
        "gap_before_method": int(first_m is not None and "G" in moves[:first_m]),
        "method_first_sentence": int(bool(moves) and moves[0] == "M"),
        "n_B": moves.count("B"), "n_G": moves.count("G"), "n_M": moves.count("M"), "n_R": moves.count("R"),
        "gap_cause": int(any(CAUSE.search(x) for x in gap_sents)),
        "insight": int(bool(INSIGHT.search(abstract))),
        "quant_result": int(any(NUMBER.search(x) for x in res_sents) or bool(re.search(r"\d+(\.\d+)?\s*(%|dB|×)", abstract))),
        "n_numbers": len(re.findall(r"\d+(\.\d+)?\s*(%|dB|×|x\b|fps|FPS|ms)", abstract)),
        "sota_claim": int(bool(SOTA.search(abstract))),
        "general_claim": int(bool(GENERAL.search(abstract))),
        "effic_claim": int(bool(EFFIC.search(abstract))),
        "hedge_n": len(HEDGE.findall(abstract)),
        "weak_n": len(WEAK.findall(abstract)),
        "code_release": int(bool(CODE.search(abstract))),
        "extensive_phrase": int(bool(EXTENSIVE.search(abstract))),
        "first_claim": int(bool(FIRST.search(abstract))),
        "realworld": int(bool(REALWORLD.search(abstract))),
        "named_in_abstract": int(bool(NAMED.search(abstract)) or bool(ACRONYM_INTRO.search(abstract))),
        "bench_named": int(bool(BENCH.search(abstract))),
        "question_n": len(QUESTION.findall(abstract)),
        "we_n": len(re.findall(r"\bwe\b", abstract, re.I)),
        "title_words": len(title.split()),
        "title_colon_name": int(bool(TITLE_COLON_NAME.search(title))),
        "title_colon_any": int(":" in title),
        "title_question": int("?" in title),
        "title_lead": (TITLE_LEAD.search(title).group(1).lower() if TITLE_LEAD.search(title) else ""),
    }
    return f
