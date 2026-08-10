# WhatsApp Group Inventory

Read-only discovery output from `GET http://127.0.0.1:8514/groups`, captured against the
real, already-paired WhatsApp Shadow Bridge session. This is a **name-based inventory only**
-- no group here has been enabled, added to the allowlist, or treated as a confirmed company,
technician, or area mapping. Every categorization below is a suggestion derived purely from
the group's display name and requires explicit owner confirmation before any code or config
change references it.

## Summary

- Total groups discovered: **372**
- Possible company job-intake groups: **7**
- Possible company closing groups: **2**
- Possible technician dispatch groups: **39**
- Possible technician closing groups: **37**
- Unknown / no business-name pattern matched: **287**
- Currently allowlisted: **1** (Sebai Augusta Dispatch -- the existing supervised-pilot group)

## How categorization was done

Purely lexical, on the group's display name only (no message content read, no history
fetched):
- Contains a known company/source keyword (`GMI`, `AGN`, `Main Leads`, `Lucky`) + a closing
  keyword -> `POSSIBLE_COMPANY_CLOSINGS`
- Contains a closing keyword (`closing(s)`, common misspellings) alone -> `POSSIBLE_TECH_CLOSINGS`
- Contains a dispatch keyword (`dispatch`, common misspellings) -> `POSSIBLE_TECH_DISPATCH`
- Contains a company keyword, or a job-intake keyword (`job receiving`, `jobs`, `leads`,
  `lead management`) -> `POSSIBLE_COMPANY_JOB_INTAKE`
- Nothing matched -> `UNKNOWN` (includes what appear to be personal/unrelated groups on this
  linked WhatsApp account -- family, social, college, event groups, etc.)

**Known ambiguous cases flagged for owner attention, not resolved here:**
- Multiple groups named `Adam - <Area>` / `ADAM <Area/region>` (Greenville, Charlotte NC,
  Asheville NC, Savannah, SC/GA, NC/SC/GA/VA/WA) appear across many different areas with no
  dispatch/closing keyword. This pattern *resembles* the confirmed `GMI Adams` company-broker
  pattern (one contact spanning many areas) but does not say "GMI" explicitly, so it is left
  `UNKNOWN` rather than assumed to be the same relationship.
- Typo'd dispatch/closing names (`Dipatch`, `Dispetch`, `clossings`) were still keyword-matched
  so they don't fall through to `UNKNOWN` by accident.

## Full inventory

| group_id | exact_group_name | currently_allowlisted | tentative_name_category | owner_confirmation_required |
|---|---|---|---|---|
| `120363429428302880@g.us` | GMI Adams job receiving | NO | POSSIBLE_COMPANY_JOB_INTAKE | YES |
| `120363427915008647@g.us` | GMI Adams Savannah Jobs | NO | POSSIBLE_COMPANY_JOB_INTAKE | YES |
| `120363146485872137@g.us` | Jobs Update 132 ❤️ | NO | POSSIBLE_COMPANY_JOB_INTAKE | YES |
| `120363167806350264@g.us` | Jobs Update 133 | NO | POSSIBLE_COMPANY_JOB_INTAKE | YES |
| `120363426349991534@g.us` | Khries Lead Management | NO | POSSIBLE_COMPANY_JOB_INTAKE | YES |
| `120363407514149856@g.us` | Mike//Adam Local Leads - Raleigh | NO | POSSIBLE_COMPANY_JOB_INTAKE | YES |
| `120363407868760676@g.us` | Schedule jobs Augusta | NO | POSSIBLE_COMPANY_JOB_INTAKE | YES |
| `120363412686198496@g.us` | Adams closings GMI All Areas | NO | POSSIBLE_COMPANY_CLOSINGS | YES |
| `120363417036944991@g.us` | AGN Delaware CLOSINGS | NO | POSSIBLE_COMPANY_CLOSINGS | YES |
| `120363403321651687@g.us` | Abozeid Arlington Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363421850567182@g.us` | Abozeid Charleston Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363401109233236@g.us` | Ahmed Delaware Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363428199635701@g.us` | Ashraf Charlotte Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363421410114618@g.us` | Bassam Alabama Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363401223938383@g.us` | Beeko Dispatch Delaware | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363405707504909@g.us` | Bekoo Augusta Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363404586604674@g.us` | Delaware dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363423140052802@g.us` | Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363418907127697@g.us` | Dispatch delaware | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363423121570395@g.us` | Dispatch Team | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363427329400350@g.us` | Dispatchers | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363406134197775@g.us` | Fares Virginia Beach Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363420206936935@g.us` | Farouk Charleston Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363377646777301@g.us` | Ismail agaza Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363346596208813@g.us` | Joe Delaware Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363425260967940@g.us` | Joe Greenville Dispatch/Saturday | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363340443536301@g.us` | Joy Locksmith Columbia Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363345820489200@g.us` | Joy Locksmith Delaware Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363367002811086@g.us` | Joy locksmith Raleigh Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363406092155275@g.us` | KhriesChicago Dispatch/Saturday | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363411941241893@g.us` | Kimo Alabama Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363418011867566@g.us` | Luxor Charleston Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363408345627351@g.us` | Luxor Savannah dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363427617650990@g.us` | Mark nj dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363403179158034@g.us` | Mohamed Delaware Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363403736659314@g.us` | Osos NJ dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363406291541239@g.us` | San Antonio Dispatch/Monday | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363424227833077@g.us` | Sebai Augusta Dispatch | YES | POSSIBLE_TECH_DISPATCH | YES |
| `120363422261595869@g.us` | Shataaa Charleston Dipatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363418771294703@g.us` | St. Pete Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363422119499970@g.us` | St. Pete Dispetch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363425255488739@g.us` | Tarek columbia dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363404172800287@g.us` | Tarek Savannah Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363407762864599@g.us` | Toto Greenville Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363410595686038@g.us` | Trio Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363424128482715@g.us` | Vancouver Dispatch | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363409763747953@g.us` | Vancouver Dispatch Hashem | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363421773052415@g.us` | Ziyadah Dispatch/Sunday | NO | POSSIBLE_TECH_DISPATCH | YES |
| `120363422531151840@g.us` | Abozeid Arlington Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363404902885686@g.us` | Abozeid Charleston Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363427497698453@g.us` | Adam - Asheville NC CLOSINGS | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363429084866103@g.us` | Adam - Charlotte NC Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363409883999573@g.us` | Adam - Greenville Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363403604981447@g.us` | Ahmed Delaware Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363363208638101@g.us` | Amir Columbia Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363428143815156@g.us` | Ashraf Charlotte Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363399822651490@g.us` | Augusta - Adam - clossings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363423141058670@g.us` | Bassam Alabama Closing | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363419496003209@g.us` | BEEKO DELAWARE CLOSINGS | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363403151465498@g.us` | Beeko Delaware Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363424417483388@g.us` | Bekoo Augusta Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363409119640702@g.us` | Cheap and fast Adam closing | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363408179176581@g.us` | Fares Virginia Beach Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363403644040859@g.us` | Farouk Charleston Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363392480509639@g.us` | Ismail Columbia Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363334123868337@g.us` | Joe Delaware Closings Joy locksmith | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363423478202519@g.us` | Joe Greenville Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363418587402372@g.us` | Joy Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363401104609706@g.us` | Khries Chicago Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363408595340686@g.us` | Kimo Alabama Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363422611738767@g.us` | Luxor Charleston Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363414378981497@g.us` | Luxor savannah closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363402764308679@g.us` | Mohamed Delaware Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363424544764208@g.us` | Omar Closings DE for Joe | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363420633655712@g.us` | Osos nj Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363410086136159@g.us` | San Antonio Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363426584048614@g.us` | Sebai augusta closing | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363423263506985@g.us` | Shataa Charleston Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363419178859376@g.us` | St Pete. Closing | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363425277781798@g.us` | Tarek columbia closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363423142361260@g.us` | Tarek Savannah Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363429123808793@g.us` | Toto Greenville Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363428396358524@g.us` | Vancouver Closing Hashem | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363406073039318@g.us` | Vancouver Closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363420900690444@g.us` | Ziyadah closings | NO | POSSIBLE_TECH_CLOSINGS | YES |
| `120363197377146431@g.us` | NEW YEAR’S EVE!!🕺😎 | NO | UNKNOWN | NO |
| `120363261107029866@g.us` | "طريق التعافي" | NO | UNKNOWN | NO |
| `120363241025228902@g.us` | "طريق التعافي" | NO | UNKNOWN | NO |
| `201115595770-1466805936@g.us` | .. | NO | UNKNOWN | NO |
| `120363045164476641@g.us` | ... | NO | UNKNOWN | NO |
| `120363026055494751@g.us` | ... | NO | UNKNOWN | NO |
| `201015806940-1503164124@g.us` | 007 | NO | UNKNOWN | NO |
| `201201979468-1542896991@g.us` | 12th 🎥 | NO | UNKNOWN | NO |
| `201201979468-1562004171@g.us` | 25/8 Training | NO | UNKNOWN | NO |
| `120363421199837215@g.us` | 28/3🤵 | NO | UNKNOWN | NO |
| `201014057570-1509960717@g.us` | 2eddeha 😎😎😎 | NO | UNKNOWN | NO |
| `201002819281-1432982324@g.us` | 2s7b so2 | NO | UNKNOWN | NO |
| `201062055597-1558479861@g.us` | 3id milad nona | NO | UNKNOWN | NO |
| `201144117774-1426610298@g.us` | 5erougaaa | NO | UNKNOWN | NO |
| `201222999906-1424114404@g.us` | 5erougat youm el sabat | NO | UNKNOWN | NO |
| `120363129796316363@g.us` | 8-May Class UK (Alex) | NO | UNKNOWN | NO |
| `201121230863-1507234929@g.us` | A7la Hezar Ba2a Wala Eh 💃 | NO | UNKNOWN | NO |
| `120363151654854592@g.us` | Abu Dhabi job vacancy | NO | UNKNOWN | NO |
| `120363409378615504@g.us` | ADAM - ASHEVILLE NC | NO | UNKNOWN | NO |
| `120363425775173739@g.us` | Adam - Charlotte NC | NO | UNKNOWN | NO |
| `120363424464824773@g.us` | Adam - Greenville | NO | UNKNOWN | NO |
| `120363406823378030@g.us` | Adam -Savanna | NO | UNKNOWN | NO |
| `120363426454925857@g.us` | ADAM SC/GA | NO | UNKNOWN | NO |
| `120363424476089511@g.us` | ADAM SUB 50% NC/SC/GA/VA/WA | NO | UNKNOWN | NO |
| `201125210910-1434928361@g.us` | Ah ya donia | NO | UNKNOWN | NO |
| `120363397505184895@g.us` | Ahmed’s Bday - Feb 21 - Grand House Lounge | NO | UNKNOWN | NO |
| `201015806940-1516795540@g.us` | Akl | NO | UNKNOWN | NO |
| `201015806940-1487315109@g.us` | Al shabab | NO | UNKNOWN | NO |
| `120363427387023802@g.us` | AL VIGITINIA SUPPORT 40% | NO | UNKNOWN | NO |
| `120363403727119661@g.us` | Alex Trainings 2 | NO | UNKNOWN | NO |
| `120363141446528441@g.us` | Annuals | NO | UNKNOWN | NO |
| `201211622197-1522910864@g.us` | Applied Art & PR Proj.🙆🏻‍♀ | NO | UNKNOWN | NO |
| `201276673753-1518425464@g.us` | Applied arts group c | NO | UNKNOWN | NO |
| `201015806940-1462656771@g.us` | Arabic | NO | UNKNOWN | NO |
| `201276322934-1569195907@g.us` | Arabic Editing Skills (M) | NO | UNKNOWN | NO |
| `201098198455-1541312576@g.us` | Arabic News Project 🎬🎤🎞🎥 | NO | UNKNOWN | NO |
| `201116656144-1507380107@g.us` | Arabic Project 📚 | NO | UNKNOWN | NO |
| `120363039883223462@g.us` | AT&T -MSS- Wave 10 | NO | UNKNOWN | NO |
| `201062215336-1634838165@g.us` | AT&T MSS Consultants | NO | UNKNOWN | NO |
| `120363022116563063@g.us` | At&t MSS wave 10 (11-Jan) | NO | UNKNOWN | NO |
| `120363400431562993@g.us` | Augusta - Adam | NO | UNKNOWN | NO |
| `201025719700-1451832147@g.us` | Aza marwan | NO | UNKNOWN | NO |
| `201008154719-1425509857@g.us` | Balba3 bokra | NO | UNKNOWN | NO |
| `201015806940-1619445144@g.us` | Bang bang | NO | UNKNOWN | NO |
| `201068935198-1567162985@g.us` | Bashmohandes fares 🎬🎥 | NO | UNKNOWN | NO |
| `201064479909-1457373280@g.us` | Biology | NO | UNKNOWN | NO |
| `201020008088-1502572263@g.us` | Birthday | NO | UNKNOWN | NO |
| `201015806940-1489696434@g.us` | Bokra 5atab | NO | UNKNOWN | NO |
| `201068848080-1425474341@g.us` | brothers | NO | UNKNOWN | NO |
| `201024257770-1583173538@g.us` | Cairo | NO | UNKNOWN | NO |
| `201277714347-1507200534@g.us` | CAS Actors 2017 Sept 1A | NO | UNKNOWN | NO |
| `201014057570-1508103210@g.us` | CAS club | NO | UNKNOWN | NO |
| `120363427586099092@g.us` | Cheap & Fast Locksmkth Savannah | NO | UNKNOWN | NO |
| `201015806940-1463427160@g.us` | Chemistry | NO | UNKNOWN | NO |
| `201015806940-1416167961@g.us` | Class 2016 ✌️ | NO | UNKNOWN | NO |
| `201027179552-1537044629@g.us` | ClassB Term 5⃣ | NO | UNKNOWN | NO |
| `201015806940-1557698034@g.us` | Clc almonyam | NO | UNKNOWN | NO |
| `201211622197-1557070430@g.us` | CLC ALUMNI FINAL | NO | UNKNOWN | NO |
| `201276322934-1556038144@g.us` | College of Maritime AAST | NO | UNKNOWN | NO |
| `201145551527-1511642414@g.us` | Comm. theories🤦🏻‍♂ | NO | UNKNOWN | NO |
| `201001606937-1555086771@g.us` | Communication group projc | NO | UNKNOWN | NO |
| `201201979468-1537719254@g.us` | Communication Technology | NO | UNKNOWN | NO |
| `201000976450-1506171008@g.us` | Communication Theories C | NO | UNKNOWN | NO |
| `120363184060648393@g.us` | Dahaab علسحطوط | NO | UNKNOWN | NO |
| `201015806940-1503351391@g.us` | Dahab | NO | UNKNOWN | NO |
| `120363419995467153@g.us` | Dahab Hangouts | NO | UNKNOWN | NO |
| `201227000234-1503266612@g.us` | Dahab 😍 | NO | UNKNOWN | NO |
| `120363422103893720@g.us` | Daniel 24/7 / Adam Columbia | NO | UNKNOWN | NO |
| `201096770448-1551436001@g.us` | Documentary | NO | UNKNOWN | NO |
| `201201979468-1550236974@g.us` | Documentary | NO | UNKNOWN | NO |
| `201207077499-1557942136@g.us` | Documentary interviews | NO | UNKNOWN | NO |
| `201096770448-1556458428@g.us` | Documentary كيلاكيت ٣ مرة | NO | UNKNOWN | NO |
| `201068935198-1559070991@g.us` | E7na el alumni tani | NO | UNKNOWN | NO |
| `20115977335-1433535883@g.us` | El fadyeeen | NO | UNKNOWN | NO |
| `201144117774-1425569924@g.us` | El maganeen | NO | UNKNOWN | NO |
| `201210702011-1556541740@g.us` | El regala | NO | UNKNOWN | NO |
| `201014057570-1530880398@g.us` | El warsha | NO | UNKNOWN | NO |
| `201010533303-1487843380@g.us` | Elda7i7a | NO | UNKNOWN | NO |
| `201002760348-1631747821@g.us` | Elsal7een💨👳 | NO | UNKNOWN | NO |
| `201002819281-1416943480@g.us` | english private | NO | UNKNOWN | NO |
| `201115416665-1505760609@g.us` | Engy's sp 6/10 | NO | UNKNOWN | NO |
| `201200445226-1525953057@g.us` | Español  "C" | NO | UNKNOWN | NO |
| `120363331858859575@g.us` | Events ⚡️ | NO | UNKNOWN | NO |
| `201015806940-1526419018@g.us` | Fam | NO | UNKNOWN | NO |
| `120363213125823187@g.us` | Family | NO | UNKNOWN | NO |
| `201020008088-1490999096@g.us` | Family | NO | UNKNOWN | NO |
| `201068848080-1469305093@g.us` | Family | NO | UNKNOWN | NO |
| `120363261526683862@g.us` | Family 😍 | NO | UNKNOWN | NO |
| `201015806940-1621598284@g.us` | FARA7 a5o madyy !! | NO | UNKNOWN | NO |
| `120363109231882161@g.us` | Fetar 2023 ✨ | NO | UNKNOWN | NO |
| `201003160441-1545404776@g.us` | Final🎬🎬 | NO | UNKNOWN | NO |
| `201000053410-1635177346@g.us` | Fitness Department Maadi | NO | UNKNOWN | NO |
| `201110077591-1465563991@g.us` | Ftar bokra | NO | UNKNOWN | NO |
| `201092498794-1558493077@g.us` | ftar el 5amis | NO | UNKNOWN | NO |
| `201201677955-1633621858@g.us` | Game over | NO | UNKNOWN | NO |
| `201128855110-1503609183@g.us` | Gaming | NO | UNKNOWN | NO |
| `201287764774-1518194103@g.us` | Gapped besties 😂👀 | NO | UNKNOWN | NO |
| `201098657799-1520968370@g.us` | Gift 50, tom tamara 2:30 | NO | UNKNOWN | NO |
| `201015806940-1431277294@g.us` | good luck | NO | UNKNOWN | NO |
| `201011556266-1570717955@g.us` | Grad.Project في حب السيسي | NO | UNKNOWN | NO |
| `201283326311-1586167275@g.us` | graduating class of 2020 | NO | UNKNOWN | NO |
| `201223226436-1581942474@g.us` | Graduation meetings | NO | UNKNOWN | NO |
| `201201979468-1570712961@g.us` | Graduation project 👨🏻‍🎓👩🏻‍🎓 | NO | UNKNOWN | NO |
| `201110222140-1476996958@g.us` | Graduation 🎓 | NO | UNKNOWN | NO |
| `97466843790-1506076947@g.us` | Group C 🎬 | NO | UNKNOWN | NO |
| `201010533303-1506165094@g.us` | Group c.t | NO | UNKNOWN | NO |
| `120363164712158957@g.us` | GYM | NO | UNKNOWN | NO |
| `201015806940-1467733885@g.us` | Gym | NO | UNKNOWN | NO |
| `201015806940-1497678517@g.us` | Gym | NO | UNKNOWN | NO |
| `201015806940-1504647356@g.us` | Gym | NO | UNKNOWN | NO |
| `120363406171412101@g.us` | Gym UFC Client | NO | UNKNOWN | NO |
| `201090151141-1502412612@g.us` | Hacienda | NO | UNKNOWN | NO |
| `201090151141-1498776768@g.us` | Hacienda bokra | NO | UNKNOWN | NO |
| `201141112259-1423240363@g.us` | Hat3mloo eh ya khwalat??? | NO | UNKNOWN | NO |
| `120363076752367711@g.us` | Hi | NO | UNKNOWN | NO |
| `201144117774-1418214891@g.us` | Iliad (Group A) | NO | UNKNOWN | NO |
| `201211622197-1541704346@g.us` | intercultural comm. | NO | UNKNOWN | NO |
| `201276322934-1520600263@g.us` | Intro to journalism 2 | NO | UNKNOWN | NO |
| `201010533303-1526404072@g.us` | Jornalism project | NO | UNKNOWN | NO |
| `120363409346625556@g.us` | Joy / Key Craft Vriginia | NO | UNKNOWN | NO |
| `120363404977680135@g.us` | Kabo Codes group | NO | UNKNOWN | NO |
| `120363406416094026@g.us` | Karim - medicine | NO | UNKNOWN | NO |
| `201200203903-1633694390@g.us` | Kaumeya Reunion | NO | UNKNOWN | NO |
| `201015806940-1469364267@g.us` | Ketf today | NO | UNKNOWN | NO |
| `120363023386208160@g.us` | Kicking Assets 😎 | NO | UNKNOWN | NO |
| `201210702011-1580827300@g.us` | King Mariouteya | NO | UNKNOWN | NO |
| `201200203903-1632663323@g.us` | Kolotat | NO | UNKNOWN | NO |
| `201010533303-1506525518@g.us` | Language for media | NO | UNKNOWN | NO |
| `120363145453149607@g.us` | Layla’s Engagement | NO | UNKNOWN | NO |
| `201276673753-1491472222@g.us` | Literary text 😇 | NO | UNKNOWN | NO |
| `120363419970570185@g.us` | Locksmith 24 seven, Philadelphia Newcastle Delaware | NO | UNKNOWN | NO |
| `201015806940-1440013128@g.us` | Ma niggas | NO | UNKNOWN | NO |
| `201119931187-1569466871@g.us` | Ma3rseeen♥️♥️♥️ | NO | UNKNOWN | NO |
| `201201979468-1518020290@g.us` | Magazine Group C | NO | UNKNOWN | NO |
| `201068848080-1443978284@g.us` | Mama | NO | UNKNOWN | NO |
| `120363044564439972@g.us` | marioma and alex bois🤠 | NO | UNKNOWN | NO |
| `120363160125566988@g.us` | Marketing Alfakhera 📉 | NO | UNKNOWN | NO |
| `120363166428740385@g.us` | Marketing prs | NO | UNKNOWN | NO |
| `201129393455-1458839229@g.us` | Marwan's birthday | NO | UNKNOWN | NO |
| `201010533303-1506968695@g.us` | Masra7 lkolia | NO | UNKNOWN | NO |
| `201010533303-1507648870@g.us` | Mass com project | NO | UNKNOWN | NO |
| `120363333641927543@g.us` | MDLBEAST events | NO | UNKNOWN | NO |
| `201201979468-1506195541@g.us` | MEDIA 2020🤷🏻‍♀ | NO | UNKNOWN | NO |
| `201111946961-1568885393@g.us` | Media 7⃣B | NO | UNKNOWN | NO |
| `201001606937-1585226071@g.us` | Media lit 🔥 | NO | UNKNOWN | NO |
| `201001606937-1589905835@g.us` | Media literacy | NO | UNKNOWN | NO |
| `201024111418-1587025022@g.us` | Media Management 12th | NO | UNKNOWN | NO |
| `201228504878-1584359626@g.us` | Media management 7th | NO | UNKNOWN | NO |
| `201066134418-1589761844@g.us` | media mangemnt final | NO | UNKNOWN | NO |
| `201023018984-1500984778@g.us` | Media presentation | NO | UNKNOWN | NO |
| `201111946961-1538042659@g.us` | Media production | NO | UNKNOWN | NO |
| `201012835011-1560422856@g.us` | Media production city | NO | UNKNOWN | NO |
| `201144117774-1451920820@g.us` | Meen hairou7 le marwan | NO | UNKNOWN | NO |
| `201019290965-1506925094@g.us` | Mickey's birthday 🎉 🎊🎈💙 | NO | UNKNOWN | NO |
| `120363361267888804@g.us` | Mike Boss Codes Group | NO | UNKNOWN | NO |
| `201144117774-1459499402@g.us` | Miss Omneya revision | NO | UNKNOWN | NO |
| `120363332333316437@g.us` | Miss Raver Door List ✨ | NO | UNKNOWN | NO |
| `120363314045322349@g.us` | Miss Raver Door List ✨ | NO | UNKNOWN | NO |
| `201006887871-1430855705@g.us` | Moustafa Koum | NO | UNKNOWN | NO |
| `201155530451-1574782207@g.us` | Mr Sameh Selim's course | NO | UNKNOWN | NO |
| `201015806940-1626616802@g.us` | Msh yala wala eh? | NO | UNKNOWN | NO |
| `201141745546-1390647453@g.us` | MUN | NO | UNKNOWN | NO |
| `201287764774-1488792841@g.us` | MyBD Alegria Thursday9/3 | NO | UNKNOWN | NO |
| `120363042033037136@g.us` | NASM | NO | UNKNOWN | NO |
| `120363024391575570@g.us` | NASM Alex 17 Jun | NO | UNKNOWN | NO |
| `201287764774-1491575067@g.us` | Nehal's sp | NO | UNKNOWN | NO |
| `120363401216276380@g.us` | NEW                 LOG | NO | UNKNOWN | NO |
| `201111946961-1537188434@g.us` | New Media | NO | UNKNOWN | NO |
| `201015806940-1480773639@g.us` | Night | NO | UNKNOWN | NO |
| `201000053410-1635275475@g.us` | Night Shift | NO | UNKNOWN | NO |
| `120363418560048640@g.us` | Nonizzz b_dayyy💕 | NO | UNKNOWN | NO |
| `201062088196-1499071396@g.us` | Novel | NO | UNKNOWN | NO |
| `201015806940-1478164596@g.us` | Oldies of 32 | NO | UNKNOWN | NO |
| `201025719700-1451734670@g.us` | Omayier's suprise party | NO | UNKNOWN | NO |
| `201096770448-1545118024@g.us` | Oral proficiency | NO | UNKNOWN | NO |
| `120363420096958542@g.us` | Outing 💃🏻 eros 3:8 sunday | NO | UNKNOWN | NO |
| `201116241155-1422818350@g.us` | OverTrain-2-Grow | NO | UNKNOWN | NO |
| `201010533303-1510574607@g.us` | Phonetics pvt | NO | UNKNOWN | NO |
| `201207077499-1554894328@g.us` | Photography Project 📸 📸 | NO | UNKNOWN | NO |
| `120363239877910116@g.us` | PLUR FAMILY | NO | UNKNOWN | NO |
| `201001606937-1526982435@g.us` | Press Kit | NO | UNKNOWN | NO |
| `201151784424-1539517950@g.us` | Projects | NO | UNKNOWN | NO |
| `201116656144-1511188221@g.us` | Public Opinion Project 📚📕 | NO | UNKNOWN | NO |
| `97466843790-1523958346@g.us` | Radio & Tv project | NO | UNKNOWN | NO |
| `201096770448-1553454925@g.us` | Radio and tv | NO | UNKNOWN | NO |
| `201003160441-1555433751@g.us` | Radio project🎙️🎙️ | NO | UNKNOWN | NO |
| `120363332167951537@g.us` | Raves | NO | UNKNOWN | NO |
| `201025719700-1536077239@g.us` | Regala | NO | UNKNOWN | NO |
| `201015806940-1559161371@g.us` | Regala bass | NO | UNKNOWN | NO |
| `201115233796-1585572658@g.us` | Research | NO | UNKNOWN | NO |
| `201201979468-1542738941@g.us` | Research methods | NO | UNKNOWN | NO |
| `201011556266-1582804850@g.us` | Research Team | NO | UNKNOWN | NO |
| `201140100883-1608475667@g.us` | Reunion | NO | UNKNOWN | NO |
| `16478249150-1511178528@g.us` | Reunion ya wlad el ws5a | NO | UNKNOWN | NO |
| `201008154719-1432115752@g.us` | Sa7el | NO | UNKNOWN | NO |
| `971562891451-1487168351@g.us` | Sahra | NO | UNKNOWN | NO |
| `201124235214-1464498673@g.us` | Sat2 | NO | UNKNOWN | NO |
| `201227000234-1502917991@g.us` | Saturday - King | NO | UNKNOWN | NO |
| `201027397190-1585076503@g.us` | Script writing | NO | UNKNOWN | NO |
| `201211669990-1603909564@g.us` | Secret tal3a | NO | UNKNOWN | NO |
| `201015806940-1427021176@g.us` | secrets | NO | UNKNOWN | NO |
| `201286559686-1442787443@g.us` | Seniors '16 | NO | UNKNOWN | NO |
| `201015806940-1517437211@g.us` | Shabab | NO | UNKNOWN | NO |
| `201201979468-1515325897@g.us` | Shabab el gam3a😊 | NO | UNKNOWN | NO |
| `201015806940-1539903277@g.us` | Shababetos | NO | UNKNOWN | NO |
| `201015806940-1452637651@g.us` | Sneak a little | NO | UNKNOWN | NO |
| `201015806940-1474745133@g.us` | Sp | NO | UNKNOWN | NO |
| `201113225582-1428346605@g.us` | Sp | NO | UNKNOWN | NO |
| `201000868014-1427116321@g.us` | Sp .... | NO | UNKNOWN | NO |
| `201111946961-1548846824@g.us` | Spanish magazine 🤷🏻‍♀ | NO | UNKNOWN | NO |
| `201094175682-1525249472@g.us` | Spanish presentation | NO | UNKNOWN | NO |
| `201150422809-1558524927@g.us` | Spanishh | NO | UNKNOWN | NO |
| `201015806940-1476871078@g.us` | Squad | NO | UNKNOWN | NO |
| `201015806940-1501245650@g.us` | Sro | NO | UNKNOWN | NO |
| `201010533303-1499018951@g.us` | Summer | NO | UNKNOWN | NO |
| `201015806940-1484946104@g.us` | Sunday | NO | UNKNOWN | NO |
| `201003386857-1505908378@g.us` | Surprise engy💃💃💃💃💃 | NO | UNKNOWN | NO |
| `201112463779-1614086257@g.us` | Ta3yenat 🥄🍽️ | NO | UNKNOWN | NO |
| `120363422956014836@g.us` | Team Alex Trainings | NO | UNKNOWN | NO |
| `120363405189361488@g.us` | Team Alex Trainings | NO | UNKNOWN | NO |
| `201140246498-1568104759@g.us` | Techne summit | NO | UNKNOWN | NO |
| `201116021666-1567591856@g.us` | Techno summit | NO | UNKNOWN | NO |
| `201116021666-1567603493@g.us` | Techno summit event | NO | UNKNOWN | NO |
| `201008155485-1487188520@g.us` | Term 1 Feb.2017 | NO | UNKNOWN | NO |
| `201203238837-1490018639@g.us` | Term 2 | NO | UNKNOWN | NO |
| `120363418872935383@g.us` | test combia | NO | UNKNOWN | NO |
| `120363397429602448@g.us` | The Happy Collective™ | NO | UNKNOWN | NO |
| `201015806940-1446290698@g.us` | The Squad 💪🏻 | NO | UNKNOWN | NO |
| `201015806940-1417166844@g.us` | The squad 😎 | NO | UNKNOWN | NO |
| `201115416665-1489612694@g.us` | Thursday | NO | UNKNOWN | NO |
| `201203908882-1513688445@g.us` | Thursday | NO | UNKNOWN | NO |
| `201203238837-1508745586@g.us` | Translation 1 | NO | UNKNOWN | NO |
| `201067114429-1430737092@g.us` | Tuesdayy | NO | UNKNOWN | NO |
| `201201979468-1556292771@g.us` | TV Finaaallll gamed 📺 | NO | UNKNOWN | NO |
| `201223226436-1577276774@g.us` | Tv&radio | NO | UNKNOWN | NO |
| `201141112259-1567446636@g.us` | Vandetta | NO | UNKNOWN | NO |
| `201012510255-1586346102@g.us` | Visual reporting section | NO | UNKNOWN | NO |
| `201207434752-1581351676@g.us` | Visual Reporting Tutor. | NO | UNKNOWN | NO |
| `120363023450740261@g.us` | Weekends 💃🏻 | NO | UNKNOWN | NO |
| `201201979468-1550304672@g.us` | Writing for media | NO | UNKNOWN | NO |
| `201096770448-1556742358@g.us` | Writing for media 🎬 | NO | UNKNOWN | NO |
| `120363134188322434@g.us` | Yehia’s SP @Mivida 26/5 | NO | UNKNOWN | NO |
| `201015806940-1467673926@g.us` | Z Team | NO | UNKNOWN | NO |
| `201015806940-1428696458@g.us` | Zaa Gang 😎💣🔫 | NO | UNKNOWN | NO |
| `201554967540-1614673111@g.us` | احلي دفعه ٢٠٢١ | NO | UNKNOWN | NO |
| `120363431862873956@g.us` | اخبار الهيكل الخدمي لزمالة المدمنين المجهولين | NO | UNKNOWN | NO |
| `201210702011-1541512154@g.us` | اشباح PubG | NO | UNKNOWN | NO |
| `120363158777520453@g.us` | اصحاب الهمم | NO | UNKNOWN | NO |
| `120363405034348275@g.us` | الاصدقاء ❤️ | NO | UNKNOWN | NO |
| `201128855110-1501683533@g.us` | الخنازير | NO | UNKNOWN | NO |
| `120363039651895024@g.us` | الدراغمة👴🏿 | NO | UNKNOWN | NO |
| `120363411053460476@g.us` | الدعم الفني لعملاء نوفا iptv | NO | UNKNOWN | NO |
| `201554967540-1614805019@g.us` | السرية التانيه | NO | UNKNOWN | NO |
| `120363210323456854@g.us` | الشـلة💃🏿 | NO | UNKNOWN | NO |
| `201129908889-1457775055@g.us` | الشلة الجاحدة 😂🖕🏻 | NO | UNKNOWN | NO |
| `120363316644327800@g.us` | اللوح الارشادية بالمصنع | NO | UNKNOWN | NO |
| `201003160441-1524504410@g.us` | المنياكوتش2.0 | NO | UNKNOWN | NO |
| `201280409704-1486155070@g.us` | الورشة | NO | UNKNOWN | NO |
| `120363429430879845@g.us` | انتظار مجموعة طريق التعافي | NO | UNKNOWN | NO |
| `120363152759292918@g.us` | اهم حاجة الصحة 😒 | NO | UNKNOWN | NO |
| `120363321560882103@g.us` | تصوير المصنع اوقست | NO | UNKNOWN | NO |
| `120363311793513350@g.us` | تصوير المصنع جولاي 24 | NO | UNKNOWN | NO |
| `120363238893877387@g.us` | حفلات تيكنوا | NO | UNKNOWN | NO |
| `120363403969169550@g.us` | ختمه قرآنيه لشافعي في الشهر الكريم | NO | UNKNOWN | NO |
| `201212910817-1619009897@g.us` | خير يارب دائما ❤️ | NO | UNKNOWN | NO |
| `201200203903-1634466848@g.us` | ربعي | NO | UNKNOWN | NO |
| `201200203903-1446067274@g.us` | سيكرت توبس | NO | UNKNOWN | NO |
| `201096770448-1566685346@g.us` | شباب الجامعة 📽 | NO | UNKNOWN | NO |
| `201001606937-1526980555@g.us` | شباب المستقبل | NO | UNKNOWN | NO |
| `201150422809-1577736583@g.us` | شباب المستقبل 2 | NO | UNKNOWN | NO |
| `201206894971-1628264825@g.us` | عمارة المعمارى برج هاء | NO | UNKNOWN | NO |
| `120363288970607194@g.us` | عمالقة الرياض الثلاثة | NO | UNKNOWN | NO |
| `120363408867622917@g.us` | عمالقة الهلع الثلاث | NO | UNKNOWN | NO |
| `120363042977823819@g.us` | عمالقة بودي جيم الاربعة | NO | UNKNOWN | NO |
| `120363046788401123@g.us` | عمالقة جولدز جيم الاربعة | NO | UNKNOWN | NO |
| `120363401583764152@g.us` | عمالقه سيدي جابر البلد | NO | UNKNOWN | NO |
| `120363400504684695@g.us` | فعاليات اتحاد طلاب تحيا مصر _ محافظة الإسكندرية | NO | UNKNOWN | NO |
| `120363166810702207@g.us` | قسم الفرح والمرح ANNUAL LEAVES 💃🏻💃🏻😂 | NO | UNKNOWN | NO |
| `201117736838-1598663758@g.us` | مستشفى الميرى التخصصى | NO | UNKNOWN | NO |
| `120363237380399014@g.us` | ‎Open this link to join my WhatsApp Community: https://chat.whatsapp.com/DCoayKwRoSrERs9HeugH5d | NO | UNKNOWN | NO |
| `201200003824-1492129525@g.us` | ☠☠ | NO | UNKNOWN | NO |
| `201111956444-1604024317@g.us` | ✖️للكبار فقط✖️ | NO | UNKNOWN | NO |
| `201110077591-1511517398@g.us` | 🍔🍟🍕🌭🍭🍻squad | NO | UNKNOWN | NO |
| `120363048883407122@g.us` | 🎉 SP 🎂 | NO | UNKNOWN | NO |
| `201015806940-1488199166@g.us` | 👬المتوحشون ‏الأربعة👫 | NO | UNKNOWN | NO |
| `201115416665-1435271253@g.us` | 🕺🏻عمالقة القومية الثلاثة 💃🏻 | NO | UNKNOWN | NO |
| `120363170375333363@g.us` | 😎 | NO | UNKNOWN | NO |
