# -*- coding: utf-8 -*-
"""8-way engagement categorisation -- VERBATIM PORT from the be10x engine's
invited_report.py (v4.1-v5.5 lineage, ported 2026-08-05).

Do not edit the regex constants, _msg_signals() or categorize() by intuition: every
alternation encodes a specific real-world failure found by reading actual transcripts
(Hindi/Tamil/Telugu handling, 'paid' vs 'high paying job', tool-price vs programme-price
questions, AV complaints that must not count as negativity). The only deliberate change
from the source: the _HAND hand-review override table ships EMPTY (those entries were
per-date corrections for historical sessions).

The eight categories, priority-ordered and mutually exclusive:
    non attended -> non chatted -> negative engagement -> purchase intent high
    -> strong interest -> moderate interest -> information seeking -> no clear intent
A row cannot reach 'purchase intent high' or 'strong interest' on presence signals alone
without actual purchase language in the text. This is deliberate -- do not 'improve' it.
"""
import re
import unicodedata

PAYRE = re.compile(r"\b(paid(?! (?:attention|heed))|payment done|payment complete|payment successful|(?:i|we)(?:'?ve| have| had)? enrolled|enrolled in(?: the)? inner|instal?lment|emi done|(?:after|already|done) paying|(?:have |already |i )?made (?:the |my )?payment|done (?:my|the|with) payment|(?:done|did|making) (?:the )?payment|payed|registration (?:completed|done)|done registration|payment (?:is )?done|booking (?:done|confirmed|amount paid)|slot booked)\b", re.I)
# payment FRICTION/attempt (hot signal): tried/asked how to pay, payment failing, UPI/OTP issues
PAYFRICT = re.compile(r"(?:\b(?:not able|unable|can ?no?t|can'?t|couldn'?t|fail(?:ed|ing)?|issue|problem|error|trying|struggling|how)\b[^.\n|]{0,40}\bpay(?:ing|ment)?\b)|(?:\bpay(?:ing|ment)?(?: link| page| option| gateway)?\b[^.\n|]{0,40}\b(?:fail(?:ed|ing|ure)?|not work\w*|issue|problem|error|declin\w*|stuck|pending)\b)|\b(?:upi|gpay|google pay|phonepe|paytm|net ?banking|debit card|credit card)\b[^.\n|]{0,30}\b(?:pay|payment|not|issues?|fail\w*|option)\b|\botp\b[^.\n|]{0,25}\b(?:not|nahi|issue)\b|\bpay(?:ing|ment)?\b[^.\n|]{0,30}\bagain\b|\basks? (?:me )?to pay\b|\bpaid (?:twice|two times|2 times|double)\b|\bdouble (?:payment|charged?)\b|\b(?:get|want|need)(?: my| a)? refund\b|\brefund (?:policy|option|process)\b|\bqr\b[^.\n|]{0,30}\b(?:error|fail\w*|not work\w*|again|scan\w*)\b|\bshow (?:the |me )?(?:the )?qr\b|\bqr code (?:once )?again\b|\bpay\w{0,5}\b[^.\n|]{0,15}\b(?:url|link)\b[^.\n|]{0,20}\b(?:wrong|not work\w*|error)\b|\bpayment\b[^.\n|]{0,25}\b(?:pending|not show\w*)\b|\b(?:issues?|problems?|errors?)\b[^.\n|]{0,30}\bpay(?:ing|ment|men)?\b|\bpay(?:ing|ment|men)?\b[^.\n|]{0,30}\b(?:issues?|problems?|errors?|fail\w*|failied|faild)\b|\bpayment kar\w{0,6} (?:paunga|dunga|karunga)\b|\btab (?:jake|ja ?ke) payment\b|\bsalary (?:aayega|milega|aane)[^.\n|]{0,30}\bpayment\b", re.I)
# ---- v4.1 full-chat semantic layer: tool-cost guard, join/EMI/price asks, strong-vs-weak negativity ----
TOOLPRICE = re.compile(r"\bfree (?:or|ya) paid\b|\bpaid (?:or|ya) free\b|\bis (?:it|this|that)(?: tool)? (?:paid|free)\b|\b(?:paid|free|premium) (?:versions?|plans?|subscriptions?|accounts?|tools?|wala|features?|packages?)\b|\b(?:this|it|that) is (?:a )?paid\b|\bpaid hai\b|\bis (?:it|this) free\b|\b(?:chat ?gpt|gpt|gemini|claude|canva|julius|comet|wispr|whispr|copilot|perplexity|midjourney|n8n|notebook ?lm|this tool|that tool|these tools?)\b[^.\n|]{0,30}\b(?:paid|free|cost|price|purchase|subscription)\b|\b(?:purchase|buy)\b[^.\n|]{0,25}\b(?:tool|domain|laptop|software|subscription)\b", re.I)
PASTGRIEV = re.compile(r"\b(?:paid|purchased|bought|taken(?: the| your)? course|enrolled|registered)\b[^.\n|]{0,70}\b(?:20\d\d|earlier|last (?:year|time)|previously|already a member)\b|\bpreviously purchased\b|\balready (?:taken|did|done)(?: the| your)? (?:course|program|workshop)\b", re.I)
JOINHOW = re.compile(r"\bhow (?:to|can (?:i|we)|do (?:i|we)|should i) (?:join|enroll|enrol|register|buy|purchase|start|sign ?up)\b|\b(?:want|like) to (?:join|enroll|enrol|register|sign ?up)\b|\b(?:can|how can) i register\b|\b(?:join|enroll|enrol|register) (?:kaise|kese|kayse)\b|\bkaise (?:join|enroll|register|le)\b|\bwhere (?:to|can i|do i) (?:join|enroll|register|pay|buy)\b|\b(?:registration|enrollment|enrolment|joining|payment) (?:link|process|page|form)\b|\bnext batch\b|\bbatch (?:start|kab)\b|\bwhen (?:does|will|is)(?: the)? (?:batch|course|program|class(?:es)?)\b[^.\n|]{0,20}\bstart\b|\bhow to make payment\b|\b(?:we|i)'?ll (?:definitely )?join\b|\bready to (?:take|join|buy|enrol(?:l)?)\b|\bsend (?:me )?(?:the )?(?:payment|registration|joining) link\b|\bcan i (?:still )?(?:join|enrol(?:l)?|register)\b|\benrol(?:l)?ment purchase\b", re.I)
EMIASK = re.compile(r"\b(?:emi|instal?lments?)\b(?![^.\n|]{0,12}\bdone\b)", re.I)
DISCOUNT = re.compile(r"\b(?:discount|coupon|offer price|scholarship|concession|price negotiable|any offer)\b", re.I)
PRICEASK = re.compile(r"(?!(?:[^.\n|]{0,40})?(?:\bkitn\w+ (?:time|der|ghant\w+)\b|\bkamat\w+\b|\btoken limits?\b))(?:\b(?:price|pricing|fees?|cost|charges?|kitna|kitne|kitni)\b|\bamt\b *\?|\bhow much\b(?! (?:longer|time|more|hours?|mins?|minutes?))|₹|\brs\.? ?\d)", re.I)
TECHNOISE = re.compile(r"\b(?:audio|voice|sound|echo\w*|mic|volume|screen|video|visible|audible|lag|buffer|network|wifi|internet|rejoin|reconnect|disconnect|hang|stuck|slow down|too fast|going (?:too )?fast|go slow|speak slowly|slowly|speed|repeat (?:that|it|again|the)|not clear|louder)\b|\bzoom\b[^.\n|]{0,20}\b(?:issue|problem|link)\b|\bawa[zj]\b|\bvery (?:much )?fast\b|\btalks? (?:so |very )?fast\b|\bresolve the (?:problem|issue)\b|\bdo the needful\b|\bvo+i+ce+\w*\b|\bwhen (?:will|does|do) (?:this|it|the )?(?:session|webinar|workshop|class)? ?(?:end|finish|over|complete)\b|\bwhen will (?:the )?(?:program|class|session|workshop) start\w*\b|\bhow much more time\b|\bwrap (?:up|it up)\b|\bwind up\b|\bis the session over\b|\bfinish it\b|\bend (?:the|this) session\b|\bwhen you will end\b|\bwhen (?:it|this) will end\b|\bstill how much time\b|\bhow much time (?:left|more|remaining)\b|\bwhen will (?:u|you) end\b|\bslower\b|\bspeak (?:\w+ ){0,2}slow\w*\b|\bplease start\b|\bstart the (?:class|session|workshop|topic)\b|\blets? start\b|\bwhen (?:will|does) (?:it|this|the \w+)? ?start\b|\bstill not started\b|\bend (?:it|this|the session) (?:at|by) \d|\bstick to (?:your|the) timings?\b|\bturn off (?:the )?music\b|\brepeat(?:ing)? (?:the )?(?:same )?(?:words?|sentences?|thing|twice)\b|\bevery single word\b|\bgiv\w* a pause\b|\bpause\b[^.\n|]{0,20}\bspeak\w*\b|\bnot able to (?:hear|see|login|join)\b|\b(?:how to )?join (?:the )?(?:chat|zoom|meeting|audio|call)\b|\bcan'?t (?:hear|see)\b", re.I)
SEVERE = re.compile(r"\b(?:scam\w*|fraud\w*|fake|cheat(?:ed|ing|ers?)?|loot|chor|jhoot\w*|fek\w+|bakwas|bakwaas|faltu|rubbish|hopeless|worst|horrible|pathetic|nonsense|useless|bek[aw]ar|misleading|clickbait|third class|liars?|lying|time ?pass|time ?wast\w*|waste of (?:time|money)|wasted my [\w ]{0,12}(?:time|hours?|money)|not interested|no thanks|crap(?:py)?(?! (?:data|the data|content from))|shit(?:ty|tt+)?\w*|bull ?shit\w*|wtf|f+u+c+k\w*|f off|get lost|ass ?holes?|mofos?|bsdk|bosdik\w*|lod[ae]\w*|gadh[ao]\w*|bewakoof|m(?:adar|other) ?chod\w*|b(?:ahan|ehen) ?chod\w*|chutiy\w*|bakchod\w*|money hungry|freaks?|shame(?:ful|less)? on you|lame|yapping|not worth(?: it)?)\b|\bmaking (?:a )?fool\b|\bnever (?:going to )?join\w*\b(?=[^.\n|]{0,30}\bworkshop|\b)|\bwast(?:e|ed|ing)\b[^.\n|]{0,25}\b(?:time|money|everyone|hours?)\b|\bnever (?:going to )?(?:attend|join)\b|\bare (?:you|u)\b[^.\n|]{0,15}\b(?:crazy|mad|insane)\b|(?:^|\s)waste\s*[.!]*\s*$|\bsell(?:ing)? your (?:products?|courses?|programs?)\b[^.\n|]{0,30}\bdisappoint\w+|\byou should be jailed\b|\bnot (?:a )?live session\b|\bmake (?:us|me|people) fool\w*\b|\b(?:this|it|its|it's|meeting|session) is (?:just )?(?:a )?(?:pre-?)?recorded\b", re.I)
MILDNEG = re.compile(r"\bboring\b|\bbor(?:e|ing)\w* (?:ho|now)\b|\birritat\w+\b|\bannoy\w+\b|\bdisappoint\w+\b|\bhard ?sell\w*\b|\bsales? pitch\b|\b(?:only|just|too much(?: of)?|sirf)\s+(?:doing\s+|about\s+)?(?:selling|marketing|promotions?|promoting|advertis\w+|bech\w*|ads?|testimonials?)\b|\bstop\b[^.\n|]{0,15}\b(?:selling|advert\w*|promot\w*|marketing|bloating|pitch\w*)\b|\b(?:why|stop) sell\w*\b|\bsell(?:ing)? your (?:products?|courses?|programs?)\b|\bstop (?:it|this|here)\b|\bcome to the point\b|\bget to (?:the )?point\b|\bmove ahead\b|\bcut it short\b|\bwrap (?:it )?up\b|\bwind up\b|\btoo long\b|\brunning too long\b|\bteaching less\b|\bless teaching\b|\badvertis\w+ more\b|\b(?:don'?t|do not|stop|without) wast\w+ [\w ]{0,10}time\b|\bcourse bech\w*\b|\btoo much time\b|\bsum (?:it )?up\b|\b(?:higher|more) promotion\b|\blo+sing patience\b|\btoo much of talking\b|\bonly (?:about )?to (?:buy|sell) your\b|\btalking so much only\b|\binstead of (?:just )?(?:marketing|selling|promoting|advertis\w+)\b|\bis (?:it|he|this|the (?:course|session|workshop)) over\b|\bcan we end\b|\bend (?:up )?(?:here|now)\b|\bbo+a?r+ing\b", re.I)
RITUALTOK = re.compile(r"^(?:yes+|ye+s*|y|no+|ok(?:ay)?|me+|swp|gd|gc|pe|bp|mb+|amb|tmb|bmb|ready+\w*|read+y+\w*|10x+|100x|1000x|bonus|action|sh|s|done|sure|agree(?:d)?|true+|clear|hind[io]|english|tamil|telugu|kannada|marathi|gujarati|bengali|punjabi|bhojpuri|malayalam|odia|kashmiri|konkani|sanskrit|\d{1,3}|[\d,.]+ ?(?:lpa|cr|lakhs?|l)|👍+|✋+|❤+|🙏+)[.!\s]*$", re.I)
POSTOK = re.compile(r"\b(?:wow+|woo+w*|amazing|awesome|excellent|great|loved? it|nice|superb?|super|fantastic|excit\w+|mind ?blow\w*|magic\w*|brilliant|perfect|helpful|goo+d|boom|really cool|very well|well done|thank(?:s| you)|fine|beautiful|lovely)\b|👏|🔥|😍|🥵|🤯", re.I)
NEGGUARD = re.compile(r"\b(?:no|without|zero) (?:time )?wast\w+\b|\bshit down\b|\bwast\w+ [\w ]{0,15}(?:without knowing|before (?:this|knowing)|till (?:now|date)|until now|so far)\b|\bhope\b[^.\n|]{0,20}\bwast\w+|\bdon'?t want to waste (?:my|our)\b|\bwaste of time (?:for |who |those )\w*|\bnot interested (?:about|in) (?:your )?(?:backgr\w*|intro\w*|stor(?:y|ies)|achievements?)\b|\b(?:those|people|ones?) who are not interested\b|\bnot interested further\b|\bnot interested in \d{1,3}%|\bnot interested\b(?=[^.\n|]{0,60}\binterested (?:only )?in\b)", re.I)
DEMOTGT = re.compile(r"\b(?:dashboard|website|web ?site|design|image|resume|ppt|slide\w*|logo|template|output|app)\b", re.I)
PAYNEG = re.compile(r"\b(?:never|not|haven'?t|didn'?t|don'?t|won'?t) (?:\w+ )?paid?\b|\bpaying job\b|\bhigh paying\b|\bwell paying\b|\b(?:create|make|build|sell|launch)\b[^.\n|]{0,25}\bpaid\b", re.I)
COURSEQ = re.compile(r"\b(?:duration(?! (?:of|for) (?:th(?:e|is|at) |today'?s )?(?:session|webinar|workshop|break|class|meeting|call))|how long\b(?=[^.\n|]{0,30}\b(?:course|program|batch|training|classes)\b)|how many (?:months|weeks)|when (?:will|does|do)(?: the)? (?:class|batch|course|program|it) start|what(?:'s| is) included|inner ?circle|career accelerator|curriculum|syllabus|batch|placement|job (?:guarantee|assistance|support)|mentor\w*|class (?:timing|schedule)s?|course (?:content|details?|structure|tenure)|next steps? (?:to|after)|slot booked|my registration|confirm\w* my|gst\b|seats? (?:left|remaining|available)|seats? (?:all )?full|registration (?:over|closed|still open|window|deadline)|early ?bird)\b", re.I)
FOLLOWUP = re.compile(r"\b(?:certificat\w+|links?|recording|notes|summary|ppt|deck|slides|bonus\w*)\b[^.\n|]{0,30}\b(?:please|pls|plz|send|share|get|where|how|not (?:working|able|generat\w+)|milega|milegi|error|vanish\w*|resend)\b|\b(?:send|share|resend|where(?:'s| is))\b[^.\n|]{0,25}\b(?:certificat\w+|links?|recording|notes|summary|ppt|deck|slides|bonus\w*)\b", re.I)
QUOTEMB = re.compile(r"(?:say(?:ing)?|use|using|typ\w+|word|phrase|call\w*|hear(?:ing)?|listen\w*|every|again)[^.\n|]{0,30}mind ?blow|mind ?blow\w*[^.\n|]{0,30}(?:again|agian|phrase|every|irritat\w*|annoy\w*)", re.I)
JOKE = re.compile(r"😂|😅|🤣|\blol+\w*\b|\blmao\b|\bha(?:ha)+h*\b|\bjk\b|\bjust kidding\b", re.I)
STRONGNEG = re.compile(r"\b(?:scam\w*|fraud|fake|cheat(?:ed|ing)?|loot|chor|bakwas|bakwaas|faltu|rubbish|hopeless|worst|horrible|pathetic|nonsense|useless|boring|bek[aw]ar|misleading|clickbait|third class|time ?pass|time ?waste|waste of time|not interested|no thanks|crap(?:py)?(?! (?:data|the data|content from))|shit(?:ty)?|disappoint\w+|shame(?:ful|less)?|yapping|pointless|worthless|senseless|not worth(?: it)?|money hungry|freaks?|irritat\w+)\b|\bwast(?:e|ed|ing)\b[^.\n|]{0,25}\b(?:time|money|everyone)\b|\b(?:only|just|too much(?: of)?|sirf)\s+(?:doing\s+|about\s+)?(?:selling|marketing|promotion|promoting|advertis\w+|bech\w*)\b|\bsales pitch\b|\bcourse bech\b|\bstop (?:it|this|here|wasting)\b|\bstop\b[^.\n|]{0,15}\b(?:selling|advert\w*|promot\w*|marketing|bloating)\b|\bhard ?sell\b|\bbull ?shit\w*\b|\bare (?:you|u)\b[^.\n|]{0,15}\b(?:crazy|mad|insane)\b|(?:^|\s)waste\s*[.!]*\s*$|\b(?:why|stop) sell\w*\b|\bsell(?:ing)? your (?:products?|courses?|programs?)\b|\bsell(?:ing)? your (?:products?|courses?|programs?)\b|\bteaching less\b|\bless teaching\b|\badvertis\w+ more\b", re.I)
SUSREC = re.compile(r"\bsame (?:video|content|recording|session)\b|\bpre-?recorded\b|\bthis is recorded\b|\bis this recorded\b", re.I)
SUSPAIR = re.compile(r"\bagain\b|\bearlier\b|\bfake\b|\bnot live\b|\bplayed\b|\brepeat\w*\b|\blast (?:time|week)\b", re.I)
PROFCTX = re.compile(r"\b(?:analyst|analytics|investigat\w+|detection|officer|team|dept|department|industry|insurance|bank(?:ing)?|compliance|security|profession\w*|manager|work(?:ing)? (?:in|as)|field|sector|role)\b", re.I)
NEGPOS = re.compile(r"\bnow (?:its |it'?s )?eas\w+|\bnot (?:at all )?boring\b|\bnever (?:a )?boring\b|\bno more boring\b", re.I)
PROFWORDS = re.compile(r"\b(?:fraud|scam|fake|security)\b", re.I)
NEGQ_TOPIC = re.compile(r"^(?:how|what (?:is|are|if)|which|can (?:i|we)|could|does|is (?:there|it|this)|where|when|why|kaise|kya)\b", re.I)
WHATA = re.compile(r"\bwhat a\b|\bkya hi\b", re.I)


# ---- v4.5 (NEG re-audit) ----
RECVETO = re.compile(r"scam|fake|fraud|caught|lying|\blie\b|100 ?%|\bsure\b|definitely|already attended|attended \w+ weeks? ago|\bi have attended\b|attended the last\b|same as|\bexact same\b|\bso it is\b[^.\n|]{0,15}record|prove|proof|\bhuman\b|scripted|1\.5x|fast ?mode|\bsold\b|shameless|foolish|\badmin\b|chat ?box|\bvery bad\b|\bbad experience\b|\bloot|cheat|\bliars?\b|\blog\b|time was|\d{1,2}:\d{2}|timestamp|crap\w*|\bshit\w*|\bworst\b|bakwa+s+|useless|rubbish|bull ?shit|pathetic|nonsense|hopeless|horrible|time ?pass|faltu|bek[aw]ar|jhooth?|fek\w+", re.I)
RECHEDGE = re.compile(r"\bi (?:think|thing|guess|feel|believe)\b|\blooks? like\b|\bseems?\b|\bfeels? like\b|\bmaybe\b|\bit seems\b", re.I)
RECQSTART = re.compile(r"^\s*(?:is|isb|can|are|does|do|was)\b", re.I)
RIGHTTIC = re.compile(r"(?:say\w*|repeat\w*|us(?:e|ing)|every (?:sentence|time|\d+ ?min\w*)|again)[^.\n|]{0,30}\b(?:right|mind ?blow|absolutely)\b|\b(?:right right|mind ?blow\w*)\b[^.\n|]{0,30}(?:again|every|irritat|annoy)", re.I)
STARTREQ = re.compile(r"\blets? start\b|\bplease start\b|\bstart (?:the|this|with|teaching|atleast)\b|\bwhen (?:it |this )?(?:will|gonna) start\b|\bwhen will (?:it|this) start\b|\bstill not started\b|\bplease start\w*\b", re.I)
PROMOW = re.compile(r"\b(?:sell\w*|promot\w*|market\w*|advertis\w*|course|program(?:me)?|pitch|bech\w*|testimonial\w*)\b", re.I)
DELIB = re.compile(r"\bwant (?:a day|time|some time) to think\b|\bneed (?:a day|time) to (?:think|decide)\b|\bthink about it\b|\bwant a day\b", re.I)
FEARRE = re.compile(r"\b(?:i'?m|i am|im)\s+(?:scared|afraid|worried)\b|\bscared (?:from|of)\b|\bdar lag", re.I)
D2SET = ('too long', 'wrap up', 'wind up', 'is it over', 'can we end', 'end here', 'end now', 'move ahead', 'come to the point', 'get to the point', 'cut it short', 'sum up', 'sum it up')
CDQ = re.compile(r"(?:class(?:es)?|course|zoom)\b[^.\n|]{0,40}record|record\w*[^.\n|]{0,30}\b(?:class(?:es)?|course)\b", re.I)

# ---- v4.4 (full category audit) ----
BUILDPAY = re.compile(r"\b(?:make|create|build|develop|design)\b[^.\n|]{0,25}\bpayment (?:app|gateway|system|website|page)s?\b", re.I)
SESSJOIN = re.compile(r"\b(?:later|tomorrow|next (?:session|week)|other slot|another slot|leave|left|missed|rejoin|remaining)\b", re.I)
PROGANCH = re.compile(r"\b(?:course|program(?:me)?|inner|batch|fee|money|salary|working|years? old|early ?bird|training)\b", re.I)
SESSDUR = re.compile(r"\b(?:duration|how long)\b[^.\n|]{0,25}\b(?:workshop|session|webinar|class|live)\b|\bappointments? today\b|\bwhen will (?:this|it|the (?:session|workshop)) (?:end|finish|get over)\b", re.I)
PROGBUY = re.compile(r"\b(?:enrol\w*|register\w*|registration|invest(?:ing|ment)?|buy(?:ing)?|purchase(?!\s+order)|book(?:ed|ing)?|seats?|batch|admission|interested|inner ?circle|accelerator|emi|instal?lments?|fees?|price|pricing|cost|charg(?:es?|ing)|how much|discount|coupon|scholarship|pay(?:ment|ing|ed)?|paid|kharid\w*|dues|early ?bird)\b|\bjoin\b(?![^.\n|]{0,20}\b(?:chat|zoom|meeting|audio|call|link)\b)", re.I)
PROGFIT = re.compile(r"\b(?:course|program(?:me)?|inner ?circle|it|this)\b[^.\n|]{0,40}\b(?:help|useful|benefit|worth|right)\b[^.\n|]{0,15}\b(?:me|for me|us)\b|\bwill i (?:be )?benefit\w*|\buseful for me\b|\bhelp me (?:get|to get|land|find)\b", re.I)
DIRECTIVE = re.compile(r"\bcome to (?:the )?point\b|\bget to (?:the )?point\b|\bmove ahead\b|\bcome to closure\b|\bgo forward\b|\bcut it short\b|\bsum (?:it )?up\b", re.I)
VERDICTS = re.compile(r"\b(?:waste|wasted|wasting|useless|worst|scam\w*|fraud\w*|fake|boring|rubbish|bakwas|pathetic|nonsense|shit\w*|crap|disappoint\w*|sell\w*|market\w*|promot\w*|advertis\w*|bech\w*)\b", re.I)
TASKCTX = re.compile(r"\b(?:taking|takes|took|i want|i am|my|prepare|create|make|build|generate|claude|chatgpt|gemini|app|tool|image|video|website|load\w*|excel|sheets?|data(?:set)?|files?|documents?|pdf)\b", re.I)
ADDONPAY = re.compile(r"paid[^.\n|]{0,30}\b(?:extra|additional(?:ly)?|for (?:the )?(?:prompt|notes?|bonus|template)s?)\b|\bpaid \d{2,3}\b(?! ?k)", re.I)
PAYQOBJ = re.compile(r"^\s*(?:is|are|isn'?t|was|does|do|were)\b|\?\s*$", re.I)
PAYACT = re.compile(r"\b(?:i|we|i'?ve|have|had|just|already|made|done|did|making)\b[^.\n|]{0,15}\b(?:paid|payment|payed)\b|\bpayment (?:is )?(?:done|complete|successful)\b|\b(?:paid|payed) (?:for|the|my|it)\b|^\s*paid\s*[.!]*\s*$|^\s*\w+ (?:paid|payed)\s*[.!]*\s*$", re.I)
_HAND_DATE = [None]
# Hand-review override table: deliberately EMPTY in this tool (the engine's entries were
# per-date corrections for specific historical sessions and do not travel).
_HAND = {}

# ---- 8-way engagement categorisation ----
QMARKS = (" how ", " what ", " which ", " when ", " where ", " why ", " kaise", " kya ",
          "can i", "can we", "can you", "can u ", "could you", "please share", "pls share", "share the",
          "send the", "is there", "will i", "will we", "do we", "does it", "tell me", "any link")
CAT_PIH = "purchase intent high"; CAT_SI = "strong interest"; CAT_MI = "moderate interest"
CAT_IS = "information seeking"; CAT_NCI = "no clear intent"; CAT_NEG = "negative engagement"
CAT_NC = "non chatted"; CAT_NA = "non attended"
CAT_ORDER = [CAT_PIH, CAT_SI, CAT_MI, CAT_IS, CAT_NCI, CAT_NEG, CAT_NC, CAT_NA]


def _ir_norm_name(raw):
    x = unicodedata.normalize('NFKC', str(raw or '')).strip().lower()
    x = re.sub(r'[^\w\s]', ' ', x, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', x).strip()


def _msg_signals(msgs):
    """v4.1: per-message semantic flags over the FULL chat (or evidence-split fallback)."""
    s=dict(pay=0,frict=0,toolp=0,griev=0,join=0,emi=0,disc=0,price=0,q=0,sneg=0,mild=0,rit=0,pos=0,tech=0,pay_sup=0)
    for m in msgs:
        m=m.replace('’',"'").replace('‘',"'")
        ml=' '+m.lower()+' '
        tool=bool(TOOLPRICE.search(m)); tech=bool(TECHNOISE.search(m))
        guard=bool(NEGGUARD.search(m)) or bool(NEGPOS.search(m)) or (bool(PROFWORDS.search(m)) and bool(PROFCTX.search(m)))
        techx = tech and (len(m) <= 60 or not SEVERE.search(m))   # long messages: only a severe verdict overrides an embedded tech/pace phrase
        sev=(not techx) and (not guard) and (bool(SEVERE.search(m)) or (bool(SUSREC.search(m)) and bool(SUSPAIR.search(m))))
        if sev and re.search(r"(?i)\b(?:don'? ?t|do not|stop|please stop|without|no) wast\w+\b|\b(?:otherwise|or else|if not)\b[^.\n|]{0,30}\bwaste\b|\bwast\w+\b[^.\n|]{0,35}\bif (?:there|you|it|we|i|th(?:e|is|at))\b|\bwhy (?:are|r) (?:you|u|we)\b[^.\n|]{0,15}\bwast|\b\d+ ?min\w{0,4} waste\b", m) and not re.search(r"(?i)scam|fraud|fake|useless|worst|rubbish|bakwas|shit|crap|fool", m):
            sev=False
            if re.search(r"(?i)echo|audio|mic|sound|volume|lag|network|wifi|internet", m): pass
            elif not PROMOW.search(m) or STARTREQ.search(m): s['dirmild']=s.get('dirmild',0)+1
            else: s['mild']+=1
        if sev and re.search(r"(?i)\bwast\w+\b", m) and STARTREQ.search(m) and not PROMOW.search(m):
            sev=False; s['dirmild']=s.get('dirmild',0)+1
        if sev:
            mt=SEVERE.search(m)
            tok=(mt.group(0) if mt else '')
            # topic/question use of scam|fraud|fake; joke-emoji quips; demo-artifact critiques -> mild at most
            if mt is not None and re.match(r'(?i)(?:scam|fraud|fake)', tok) and NEGQ_TOPIC.search(m.strip()) and not WHATA.search(m): sev=False
            elif mt is not None and re.match(r'(?i)(?:scam|fraud|fake)', tok) and '?' in m and re.search(r'(?i)\b(?:chat ?gpt|perplexity|claude|gemini|linkedin|job profiles?|data|detect\w*|identify|filter|email|n8n|automat\w*|connect\w*|website|app|account|hack\w*|safe|secur\w*)\b', m): sev=False
            elif JOKE.search(m) and len(m)<=40 and not re.search(r'(?i)\b(?:nice|super|great|wow|lovely|wah|what a)\b\W{0,6}\b(?:scam\w*|fraud|fake)', m): sev=False
            elif DEMOTGT.search(m) and len(m)<=30: sev=False
            elif mt is not None and mt.group(0).lower().startswith('cheat') and re.search(r'(?i)cheat\s?-?\s?sheet', m): sev=False
            elif mt is not None and mt.group(0).lower().startswith('shit') and re.search(r'(?i)craz+y+', m): sev=False
            elif mt is not None and 'record' in mt.group(0).lower() and CDQ.search(m) and ('?' in m or ' or ' in m.lower()): sev=False; s['cdq']=s.get('cdq',0)+1
            elif mt is not None and 'record' in mt.group(0).lower() and (re.search(r'(?i)\bor live\b|\blive or\b', m) or re.search(r'(?i)record\w*[^.\n|]{0,15}(?:right|na|no)\s*\?*\s*$', m) or m.strip().endswith('?') or RECQSTART.search(m) or RECHEDGE.search(m)) and not RECVETO.search(m): sev=False; s['q']+=0
            elif mt is not None and mt.group(0).lower().startswith('not interested') and re.search(r"(?i)not interested in (?:the |your )?(?:whole|entire|full|complete)|not interested[^.\n|]{0,40}\bbut\b|not interested in (?:creating|making|building|generating|watching|seeing|hearing)|right now[^.\n|]{0,25}not interested|not interested[^.\n|]{0,15}(?:right now|for now|yet|at the moment)|in ?case[^.\n|]{0,30}not interested|if[^.\n|]{0,25}not interested", m): sev=False
            elif FEARRE.search(m): sev=False
            elif mt is not None and re.match(r'(?i)(?:scam|fake|fraud)', mt.group(0)) and RECHEDGE.search(m) and POSTOK.search(m): sev=False; s['mild']+=0
            elif mt is not None and re.search(r'(?i)wast|time ?pass', mt.group(0)) and re.search(r'(?i)\bi loved? (?:it|this)\b|\blove it but\b', m): sev=False; s['mild']+=1
            elif mt is not None and re.match(r'(?i)f+u+c+k|wtf', mt.group(0)) and not re.search(r'(?i)fuck(?:ing)? ?(?:off|you|u\b|this|it\b)', m) and (re.search(r'🔥|😍|🥵|🤯', m) or re.search(r'(?i)\b\w+ as fuck\b', m) or (POSTOK.search(m) and not re.search(r'(?i)wast|nothing|worst|scam|fake|shit|bull|crap|useless', m))): sev=False
            elif len(m.strip())<=12 and len(msgs)>=3 and not re.search(r'(?i)\b(?:nice|super|great|wow|lovely|wah|what a)\b\W{0,6}\b(?:scam\w*|fraud|fake)', m) and (POSTOK.search(' '.join(x for x in msgs if x is not m)) or all(RITUALTOK.match(x.strip()) for x in msgs if x is not m)): sev=False; s['mild']+=1; s['quip']=s.get('quip',0)+1
        praise = bool(POSTOK.search(m)) and not QUOTEMB.search(m) and not SEVERE.search(m)
        if sev: s['sneg']+=1
        elif (not techx) and (not guard) and MILDNEG.search(m) and not praise:
            _mh = MILDNEG.search(m).group(0).lower()
            if _mh in ('too long','too much time') and TASKCTX.search(m):
                pass
            elif _mh.startswith(('irritat','annoy')) and (QUOTEMB.search(m) or RIGHTTIC.search(m)) and not SEVERE.search(m):
                s['dirmild'] = s.get('dirmild',0)+1
            elif (DIRECTIVE.search(m) or _mh in D2SET) and not VERDICTS.search(m) and not re.search(r"(?i)\b(?:course|program|ads?|pitch)\b", m):
                s['dirmild'] = s.get('dirmild',0)+1
            else:
                s['mild']+= 2 if (len(m)>=80 and '?' not in m) else 1
                if re.search(r"(?i)\b(?:session|workshop|webinar|class|you|your|this|it|ad\w*|promo\w*|market\w*|sell\w*|course|pitch|intro\w*|guys)\b", m): s['mild_tgt']=s.get('mild_tgt',0)+1
        if sev and re.search(r'(?i)record', m): s['sneg_rec']=s.get('sneg_rec',0)+1
        if sev and mt is not None and mt.group(0).lower().startswith('no thanks'): s['nothanks']=1
        if sev and re.search(r'(?i)\bwast|useless|time ?pass', (mt.group(0) if mt else '')) or (sev and len(m.strip())<=8 and re.match(r'(?i)\s*(?:f+u+c+k+|wtf)\W*$', m.strip())): s['sneg_av']=s.get('sneg_av',0)+1
        if DELIB.search(m): s['delib']=1
        if RITUALTOK.match(m.strip()): s['rit']+=1
        if praise: s['pos']+=1
        if PASTGRIEV.search(m): s['griev']+=1
        elif PAYFRICT.search(m) and not tool and not PAYNEG.search(m) and not BUILDPAY.search(m): s['frict']+=1
        elif (PAYRE.search(m) or re.match(r'(?i)\s*(?:booked|enrolled)\s*[.!]*\s*$', m)) and not tool and not PAYNEG.search(m):
            if sev: s['pay_sup']+=1
            elif PAYQOBJ.search(m) and not PAYACT.search(m): s['toolp']+=1
            else:
                s['pay']+=1
                if ADDONPAY.search(m): s['addon']=s.get('addon',0)+1
        if tool: s['toolp']+=1
        if sev:
            pass  # angry messages don't produce asks: no join/emi/disc/price credit from accusations
        elif JOINHOW.search(m) and not re.search(r"(?i)\bjoin (?:the )?(?:chat|zoom|meeting|audio|call|class today|link)\b|\bstart (?:it|this|the (?:tool|app|website|recording|video))\b|\bstart\b[^.\n|]{0,12}\bwhen writing\b", m) and not (SESSJOIN.search(m) and not PROGANCH.search(m)): s['join']+=1
        if EMIASK.search(m) and not sev: s['emi']+=1
        if DISCOUNT.search(m) and not sev and not tool and not re.search(r"(?i)\b(?:coupon code for|pro coupon|lovable|swiggy|zomato|flight|ticket|payout|discount card|scholarship program\b.{0,20}(?:austria|abroad|ms\b))\b|\b(?:create|apply\w*|book)\b[^.\n|]{0,20}\bdiscount\b", m): s['disc']+=1
        if PRICEASK.search(m) and not tool and not sev: s['price']+=1
        if tech: s['tech']+=1
        if not tech and (('?' in m) or any(k in ml for k in QMARKS)):
            s['q']+=1
            if not (MILDNEG.search(m) or SEVERE.search(m)): s['qc']=s.get('qc',0)+1
    return s


def categorize(row, ix, msgs=None, orphan=False, no_text=False):
    """v4.1: bucket an attendee/walk-in row into one of 8 engagement categories.
    Chat-semantic layer runs over the attendee's FULL chat messages (msgs);
    falls back to the evidence-string split when msgs is None.
    Mutually exclusive, priority-ordered: non chatted -> negative engagement (STRONG negativity
    dominates + zero buying signals; pace/AV feedback and question-topic words never count)
    -> purchase intent high (payment confirm/friction, how-to-join, EMI/discount ask, D1>=9,
    2+ intent msgs, corroborated decision-ask) -> strong interest -> moderate interest
    -> information seeking (incl. tool-cost questions) -> no clear intent.
    ('non attended' is assigned to no-show rows by the caller.)"""
    def g(c):
        if c not in ix: return 0.0
        v = row[ix[c]]
        try: return float(v) if v not in (None, "") else 0.0
        except Exception: return 0.0
    def st(c):
        return str(row[ix[c]] or "") if c in ix else ""
    chatted = st("Chatted?").strip().lower() == "yes"
    attp = g("% attended")
    cta = st("Stayed to CTA").strip().lower() == "yes"
    pri = st("Present at pricing").strip().lower() == "yes"
    watch = []
    if attp > 0: watch.append("watched %d%% of session" % round(attp))
    if pri: watch.append("present at pricing")
    if cta: watch.append("stayed to CTA")
    _hk = (_HAND_DATE[0], _ir_norm_name(row[ix["Attendee"]] if "Attendee" in ix else ""))
    if _hk in _HAND:
        return _HAND[_hk]
    if not chatted and not (orphan and msgs):
        return CAT_NC, ("no chat messages; " + ", ".join(watch)) if watch else "no chat messages; barely present"
    if no_text:
        # v5.5: the engine counted chat for this person but NO message could be attributed
        # to their contact (same-name registrants in one room). Cap at "no clear intent".
        b = "engine counted chat but no message could be attributed to this contact (same-name ambiguity); label capped"
        if watch: b += "; " + ", ".join(watch)
        return CAT_NCI, b
    d1 = g("D1"); d3 = g("D3"); d4 = g("D4"); d5 = g("D5"); d7 = g("D7")
    im = g("Intent msgs"); mm = g("Meaningful msgs"); neg = g("Neg msgs")
    full = msgs is not None
    if msgs is None:
        ev = st("Strongest evidence (chat)")
        msgs = [p for p in (q.strip().strip('"') for q in ev.split(' | ')) if p]
    s = _msg_signals(msgs)
    if s['pay'] and (s['sneg'] + s['mild']) >= 5 and not (s['join'] or s['emi'] or s['disc'] or s['price'] or s['frict']):
        s['pay_sup'] += s['pay']; s['pay'] = 0   # angry paid-registrant: 'paid' mentions are grievance, not confirmation
    if s['sneg'] >= 1 and 3 * s['sneg'] >= max(mm, 1) and not (s['pay'] or s['frict'] or s['price']):
        s['join'] = s['emi'] = s['disc'] = 0     # scam-rant mentioning EMI/joining is not purchase intent
    buy_text = s['pay'] or s['frict'] or s['join'] or s['emi'] or s['disc'] or s['price']
    no_buy = (not buy_text) and d1 == 0 and im == 0 and d7 == 0 and d3 == 0
    # dominant strong negativity where any 'buying language' is only angry paid-mentions (suppressed):
    # engine dims fired on those same words, so allow NEG despite d1/d3.
    neg_dom = (not buy_text) and im == 0 and d7 == 0 and s['join'] == 0 and (s['sneg'] >= 2 and 3 * s['sneg'] >= max(mm, 1) or (s['sneg'] == 1 and mm <= 2 and len(msgs) <= 6 and s.get('qc', 0) == 0))
    if s['griev'] and not s['frict'] and (no_buy or neg_dom):
        b = "existing-customer grievance (paid earlier, unresolved issue)"
        if watch: b += "; " + ", ".join(watch)
        return CAT_NEG, b
    # v4.2: severe accusations/abuse/waste-verdicts always dominate; mild pitch-fatigue
    # needs dominance over the person's meaningful content, and heavy ritual participation
    # or explicit praise outweighs it.
    subst = max(1, len(msgs) - s['rit'] - s['tech'])
    mild_neg = (s['sneg'] == 0) and (s['mild'] >= 3 or (s['mild'] >= 2 and s['mild'] >= 0.5 * max(mm, 1)) or (s['mild'] >= 1 and subst <= 3 and mm <= 2 and s.get('mild_tgt', 0) > 0))
    if mild_neg and (s['rit'] >= 6 and s['mild'] <= 3): mild_neg = False
    if mild_neg and s['pos'] > s['mild']: mild_neg = False
    if mild_neg and s.get('qc', 0) >= 1 and s['mild'] <= 1: mild_neg = False
    if mild_neg and s['sneg'] == 0 and s.get('qc', 0) >= 3: mild_neg = False   # v4.5: engaged asker
    if mild_neg and s['sneg'] == 0 and s.get('delib'): mild_neg = False
    if mild_neg and s['mild'] == 1 and s.get('quip', 0) >= 1 and s['rit'] >= len(msgs) - 1: mild_neg = False
    _recveto_chat = any(RECVETO.search(x) for x in msgs)
    if (s['sneg'] == 1 or (s['sneg'] == 2 and s['pos'] >= 1)) and s['mild'] == 0 and not s['griev'] and mm >= 6 and s.get('qc', 0) >= 2 and s.get('sneg_rec', 0) == s['sneg']:
        pass  # v4.4/v4.5: recorded-suspicion inside a substantive, question-rich chat is mixed engagement, not NEG
    elif s['sneg'] == 1 and s.get('sneg_rec', 0) == 1 and s['mild'] == 0 and not s['griev'] and not _recveto_chat:
        pass  # v4.5: one flat/hedged 'recorded' remark, zero accusation words, nothing else negative -> not NEG
    elif s['sneg'] == 1 and s.get('nothanks') and s['pos'] >= 2:
        pass  # v4.5: soft decline wrapped in praise ('LOVED IT ... no thanks') -> not NEG
    elif s['sneg'] == 1 and s.get('sneg_av', 0) >= 1 and s['mild'] <= 1 and s.get('tech', 0) >= 3 and not _recveto_chat and not any(re.search(r"(?i)\b(?:sell\w*|promot\w*|market\w*|advertis\w*|bech\w*)\b", x) for x in msgs):
        pass  # v4.5: waste/expletive inside an AV-problem chat is stream frustration, not program negativity
    elif s.get('delib') and s['sneg'] == 0:
        pass  # v4.5: 'need a day to think' is a purchase deliberation, not negativity
    elif (no_buy or neg_dom) and (s['sneg'] >= 1 or mild_neg):
        if s['sneg'] >= 1:
            b = "%d strongly negative/complaint msg(s), negativity dominates, zero buying signals" % int(s['sneg'])
        else:
            b = "%d pitch-fatigue/complaint msg(s) dominate the chat, zero buying signals" % int(s['mild'])
        if mm: b += "; %d meaningful msg(s)" % int(mm)
        return CAT_NEG, b
    t = []
    if s['pay']: t.append("payment-confirmation in chat")
    if s['frict']: t.append("payment friction/attempt (tried or asked how to pay)")
    if s['join']: t.append("asked how to join/enroll")
    if s['emi']: t.append("EMI/installment ask")
    if s['disc']: t.append("discount ask")
    if d1 >= 9: t.append("strong buying language (D1=%d)" % int(d1))
    elif d1 > 0: t.append("buying language (D1=%d)" % int(d1))
    if im >= 2: t.append("%d intent msgs" % int(im))
    elif im == 1: t.append("1 intent msg")
    if d7 > 0: t.append("decision-ready (payment/registration ask)")
    if d3 > 0: t.append("urgency/timeline language")
    if d5 > 0: t.append("practical purchase objection")
    ctx = [x for x in (("present at pricing" if pri else ""), ("stayed to CTA" if cta else "")) if x]
    if neg > 0 or s['sneg'] > 0 or s['mild'] > 0: ctx.append("%d negative msg(s) (mixed tone)" % int(max(neg, s['sneg'] + s['mild'])))
    courseq = any(COURSEQ.search(m) and not TOOLPRICE.search(m) and not SESSDUR.search(m) for m in msgs) or bool(s.get('cdq'))
    fup = sum(1 for m in msgs if FOLLOWUP.search(m))
    corrob = bool(buy_text or s['price'] or courseq)
    progbuy = (not full) or corrob or s['toolp'] > 0 or any((PROGBUY.search(x) and not TOOLPRICE.search(x) and not PAYNEG.search(x) and not BUILDPAY.search(x) and not re.search(r"(?i)purchase order", x)) for x in msgs)
    progfit = (full and any(PROGFIT.search(x) for x in msgs)) or bool(s.get('delib'))
    dims_ok = progbuy and not (full and s['toolp'] > 0 and not corrob)
    d7_corrob = d7 > 0 and corrob
    im_pih = im >= 2 and corrob   # v4.4: engine-dim self-corroboration removed; text anchors only
    if courseq: t.append("course-detail ask")
    if fup and not corrob: t.append("%d follow-up ask(s) (certificate/link/notes)" % fup)
    if s.get('addon') and s['pay'] and not (s['frict'] or s['join'] or s['emi'] or s['disc'] or courseq):
        t = [x if x != "payment-confirmation in chat" else "paid registration add-on (fulfilment follow-up)" for x in t]
    if s['pay'] or s['frict'] or s['join'] or s['emi'] or s['disc'] or courseq or (d1 >= 9 and dims_ok) or im_pih or d7_corrob or (d1 > 0 and d3 > 0 and dims_ok):
        return CAT_PIH, "; ".join(t + ctx)
    nonrit = [m for m in msgs if not RITUALTOK.match(m.strip())]
    _nontech = [m for m in nonrit if not TECHNOISE.search(m)]
    techdom = bool(nonrit) and len(_nontech) == 0 and s['tech'] >= 1
    _sub = [m for m in _nontech if not (POSTOK.search(m) and len(m) < 80 and '?' not in m) and len(m.strip()) > 3 and not MILDNEG.search(m)]
    praisedom = full and bool(nonrit) and len(_sub) == 0 and not buy_text and not s['price']
    if (techdom or praisedom) and d4 == 0 and not buy_text:
        b = "only technical/AV/pace messages and generic chat" if techdom else "only praise/AV/generic chat, no substantive signal"
        if watch: b += "; " + ", ".join(watch)
        return CAT_NCI, b
    if s['price']: t.append("program price ask")
    if s['price'] or ((d1 > 0 or im >= 1 or d3 > 0 or d5 > 0 or d7 > 0) and (dims_ok or progfit)):
        return CAT_SI, "; ".join(t + ctx)
    if d4 > 0: t.append("use-case/profession fit (D4=%d)" % int(d4))
    if mm >= 3: t.append("%d meaningful msgs" % int(mm))
    if d4 > 0 or mm >= 3:
        return CAT_MI, "; ".join(t + ctx)
    if (mm >= 1 or s.get('qc', 0) >= 1) and (s.get('qc', 0) > 0 or s['toolp'] > 0):
        b = "asks content/how-to questions (%d meaningful msg(s)), no buying language" % int(mm)
        if s['toolp'] and not s['q']: b = "asks tool-cost/availability questions (not program intent)"
        return CAT_IS, b + (("; " + "; ".join(ctx)) if ctx else "")
    if mm >= 1 and cta and pri and (_sub or not full):
        return CAT_MI, ("chatted (%d meaningful msg(s)); " % int(mm)) + "; ".join(ctx)
    b = "only greetings/generic chat, no substantive signal"
    if s['tech']: b = "only technical/AV messages and generic chat"
    if watch: b += "; " + ", ".join(watch)
    return CAT_NCI, b


# ---------------------------------------------------------------- row/ix adapter
_FIELDS = ["Attendee", "Chatted?", "% attended", "Stayed to CTA", "Present at pricing",
           "D1", "D3", "D4", "D5", "D7", "Intent msgs", "Meaningful msgs", "Neg msgs"]
_IX = {f: i for i, f in enumerate(_FIELDS)}


def categorize_person(name, engine_chatted, pct_att, cta, pricing, dims, counts,
                      msgs, orphan=False, no_text=False):
    """Build the (row, ix) pair categorize() expects and call it.

    engine_chatted: the ENGINE-style name-join flag (did this person's display name chat
    in their room, resolved to them as longest-present record) -- NOT the output column.
    cta / pricing: True / False / None (None when no offsets were supplied -> field blank,
    which categorize treats as 'no').
    dims: {'D1','D3','D4','D5','D7'}; counts: {'intent','mean','neg'}.
    CRITICAL: always pass the real per-line message list in msgs, and no_text=True for a
    person whose chat exists but could not be attributed -- categorize(msgs=None) drops
    its text-corroboration guards and over-reports strong interest."""
    row = [
        name,
        'Yes' if engine_chatted else 'No',
        "" if pct_att is None else pct_att,
        '' if cta is None else ('Yes' if cta else 'No'),
        '' if pricing is None else ('Yes' if pricing else 'No'),
        dims.get('D1', 0), dims.get('D3', 0), dims.get('D4', 0),
        dims.get('D5', 0), dims.get('D7', 0),
        counts.get('intent', 0), counts.get('mean', 0), counts.get('neg', 0),
    ]
    return categorize(row, _IX, msgs=msgs, orphan=orphan, no_text=no_text)
