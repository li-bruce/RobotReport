# Login artwork

Generated and edited with the built-in imagegen tool. The administrator form is live HTML with mostly transparent glass styling, not part of the raster. It is vertically centered on the right on desktop and centered below the title on mobile. The illustration stays behind the form without adding space to avoid overlap. The authenticated dashboard keeps its operational layout.

- [Desktop background](../app/static/brand/gatherspace-login-desktop.png): 1672 × 941.
- [Mobile background](../app/static/brand/gatherspace-login-mobile.png): 941 × 1671.
- Responsive backgrounds and glass surface: `app/static/gatherspace.css`.
- Loading lifecycle: `app/static/login-screen.js`. The initial HTML shows a small CSS-only brand animation while the selected responsive image loads and decodes, then fades into the login page. Loaded images are reused within the page. Slow or failed requests offer a plain-background login; failed requests can also be retried. Screen changes select the matching asset without clearing the form. Reduced-motion preferences disable the animation.

One central management hub connects directly to four sports venues (badminton, table tennis, basketball and a gym) and two chat groups through six independent branches. Badminton remains the foreground scene on the left. The mobile version is separately composed with space for the title above and form below.

## Court and movement references

- [BWF TV: World Tour Finals 2024 women's doubles highlights, at 00:33](https://www.youtube.com/watch?v=LmKah_tkwcg&t=33s): a frame captured from the actual playing video to guide full-court markings and four-player orientation.
- [Sport England: Sports Halls Design and Layouts, page 43](https://sportengland-production-files.s3.eu-west-2.amazonaws.com/s3fs-public/sports-halls-design-and-layouts-2012.pdf): badminton court plan.
- [FIBA Official Basketball Rules 2024](https://assets.fiba.basketball/image/upload/documents-corporate-fiba-official-rules-2024-v10a.pdf): basketball court diagram.
- [ITTF Statutes 2023, section 2.1](https://db.ittf.com/sites/default/files/public/2023-06/2023_ITTF_Statutes_tracked_changes_version.pdf): table and net layout.

The real match is used as a reference for the local badminton redraw. The court, net and four players are then oriented together along the exterior building's long axis. Broadcast logos, scores, spectator stands and athlete likenesses are not included in the illustration. Reference screenshots and PDF pages are kept outside the public project. Court geometry and human poses are visually reviewed illustration details, not measured sports-facility drawings.

## Key prompt set

### Desktop composition

```text
Use case: stylized-concept. Asset: replacement 16:9 desktop login background, 1920x1080, for the existing GatherSpace website. Image 1 is STYLE REFERENCE ONLY. Rebuild the layout and court markings from scratch; do not inherit its incorrect lines or chain connections. Retain premium soft 3D architectural miniatures, pale blue-white seamless ground, matte teal, warm oak, restrained amber, gentle daylight, physically plausible people. No text, no logo, no watermark and NO painted login form.

ABSOLUTE TOPOLOGY: ONE central management hub with calendar and reception desk, surrounded by exactly SIX separate satellite islands: FOUR sports venues (badminton, table tennis, basketball, gym), and TWO distinct community chat groups. There must be exactly SIX independent teal paths, each starting directly at a different port on the CENTRAL hub and ending at ONE satellite. A clear star diagram, like a six-spoke wheel: central hub -> badminton; central hub -> table tennis; central hub -> basketball; central hub -> gym; central hub -> chat group A; central hub -> chat group B. Never connect satellite to satellite. Never join paths away from the hub, no shared trunk or ring road. Keep all six entire paths visible and separately traceable.

Composition for actual HTML overlay: leave the top-left x=3–40%, y=3–15% quiet for the real title, and the upper-right rectangle x=77–99%, y=2–44% EMPTY light background for the existing real login box. Hub centered near x=53%,y=61%. Arrange satellites around it: large BADMINTON in lower left x=3–43%,y=58–94%, TABLE TENNIS upper left x=3–26%,y=27–49%, BASKETBALL upper mid-left x=29–48%,y=18–40%, GYM upper middle x=54–71%,y=20–40%; CHAT GROUP A middle-right x=77–97%,y=49–68%, CHAT GROUP B lower-right x=75–97%,y=77–95%. Exactly one small white robot plus two seated people and a simple upright chat-message card in EACH group island. The two groups are distinct endpoints, with separate connections. No duplicate sports.

COURT ACCURACY IS ESSENTIAL. Use a slightly elevated isometric architectural view so all markings are legible. Construct markings as precise straight geometric lines on each planar court BEFORE placing people.

BADMINTON: a true long rectangular 13.40 m x 6.10 m court, length-to-width 2.20:1 before perspective. Treat its two short ends as y=0 and y=13.40, width x=0..6.10. White outer rectangle. Two inner singles sidelines at x=0.46 and x=5.64, both run the entire length. Only TWO short-service transverse lines at y=4.72 and y=8.68 (1.98 m each side of the net). Only TWO doubles rear-service transverse lines at y=0.76 and y=12.64. Two center-service line segments at x=3.05: y=0..4.72 and y=8.68..13.40; these MUST STOP at the short-service lines and must NOT continue through the central forecourt. ONE real net spanning across width at y=6.70, with its posts at the doubles sidelines. No center-floor painted stripe under the net. NO extra tennis service boxes, circles, random grids or diagonal lines. Make the full boundary and line system visible. Four sparse adults, two on each side of the net, normal balanced ready positions. Each adult exactly two arms/two legs and only ONE badminton racket in one hand. No jumping, no intersecting bodies.

TABLE TENNIS: exactly one dark teal rectangular table 2.74 m x 1.525 m, white perimeter lines, just ONE thin longitudinal doubles centre line dividing left/right halves, ONE low net across the midpoint perpendicular to its long axis. No additional transverse painted line or grid. Two players at opposite short ends, one paddle each.

BASKETBALL: a clearly recognizable single-hoop FIBA-style HALF COURT (roughly 14 m long x 15 m wide, not a tiny badminton rectangle). ONE hoop at the center of baseline. A rectangular key 4.90 m wide, free-throw line 5.80 m from baseline, free-throw semicircle at its outer end, one small no-charge semicircle beneath basket, and one large three-point arc centered under the basket joining two short parallel corner lines leading to the baseline. Use the near semicircle of center court at the opposite edge. No arbitrary concentric circles, no tennis lines. Two natural standing players and a single ball.

GYM: plain oak floor with NO court markings; a stationary bike with one normally seated rider, one dumbbell rack and an exercise bench. Keep human anatomy and equipment rational.

Make badminton the largest and first foreground subject. Keep all sports courts separate from circulation paths. Beautiful calm professional miniature visualization, light glass-like material accents, clean composition, realistic anatomy and clear six-spoke architecture.
```

### Badminton redraw from a real doubles match

```text
局部重绘第一张管理平台插图左下角的羽毛球场。第二张是从BWF官方真实女双比赛截取的全场画面，请以真实比赛画面为准确的运动学和场线参考，而不是沿用第一张错误的羽毛球场线。

只替换第一张图里的羽毛球场内部。允许改变这块球场的局部观察角度和四人的站位，让它像第二张比赛画面那样能完整看清场地；保留左下角大平台的整体位置和尺寸，保留木墙、窗、植物和右侧球拍架。乒乓球、篮球、健身房、两个聊天群、中央管理岛、六条直接连接、留白、整张构图和1672x941画布全都不变。

把第二张真实画面的完整场地布局和四人隔网相对的关系，转换成第一张的精致温暖3D建筑微缩模型风格，地面改为青蓝色、标线白色。球场采用略偏正面的高机位视角，近处两人背对镜头面向远端，远处两人面向近处，两队中间只有一张横跨场地短边的球网。不要为了硬保留旧视角而画错线或把人朝向队友。可以直接按真实比赛图的全场透视来重建这一块。

必须从真实比赛图逐条对应：
- 四周外边框；
- 两条贯穿全长的内侧单打边线；
- 球网两侧各有一条完整的前发球线，二者平行于球网、分别在网两边相同距离处，清晰可见；
- 两条靠近两端底线的双打后发球线；
- 每半场中线从底线只延伸到前发球线，球网两边的前发球区不能有中线。
也就是两条前发球线之间留出一个完整的无中线区域，网在该区域中央。不能漏掉任一前发球线。参考比赛截图里的真实线条连接关系，不要臆造其他线。

四个人仍用原插画的蓝衣、黄衣以及两个白衣，可重新安排站位。动作参考真实比赛帧的接球准备姿势，四个人都注视球网另一侧，近侧两人背朝镜头、远侧两人面向近侧，不看队友。每人恰好两手两脚、一只球拍，手部自然。只保留一只小羽毛球。不要复制真实运动员面孔、比赛赞助文字、商标、比分牌或观众，只借鉴场地、朝向和自然动作。完整场地清晰易读，风格与原图一致。
```

### Racket and far-court divider correction

```text
Two tiny corrections to this illustration, ONLY inside the lower-left badminton court. Preserve the newly correct front-facing full-court view, the TWO transverse short service lines, and all other details.

1. The near-left YELLOW-SHIRT player incorrectly holds TWO rackets. Remove the racket in her LEFT hand (the small racket on the LEFT of her head/body), and give that left hand a natural empty relaxed balancing pose. KEEP ONLY the one racket in her RIGHT hand, on the RIGHT side of her body. Exactly ONE racket for each of all four players.
2. Add the missing CENTER SERVICE LINE in the FAR HALF of the badminton court: a straight white line along the long axis, from the midpoint of the FAR short baseline to the midpoint of the FAR short service line. It stops at that far short service line; it must not reach the net. It mirrors the already-correct center divider in the NEAR half. Do not add any other line.

No changes to player positions or facing directions, no changes to the net, no changes to any other court markings. No changes outside this badminton scene. Keep the entire 1672x941 image, six spokes and other islands exact.
```

### Whole-court alignment to the building

```text
整体旋转对齐，局部修改第一张图左下的羽毛球场。现在这块球场错误地正对镜头、纵向摆放，没有沿外部建筑。请把“整片羽毛球地面 + 全部白色场线 + 球网 + 四名运动员”当作一个刚性整体，在水平地面上转向，使它与现有外部建筑、台座的横向长边完全一致。不要只旋转球网，不要让人物留在旧朝向。

明确方向：球场两条长边应平行于植物木墙从左上向右下延伸的那条长边，场地长轴是屏幕 左上 ↘ 右下，横向铺满现有长方形台座；两条短底线则是 左下 ↗ 右上。球网横跨短宽度，也是 左下 ↗ 右上 的 / 方向，位于长轴中点。不能再像现在这样让球场的长轴几乎竖直朝向镜头。

所有元素一起按这个方向转过去：
- 原来远端的蓝衣和白衣两人转到左上半场，两人都面对右下半场、看向网对面的对手；
- 原来近端的黄衣和白衣两人转到右下半场，两人都面对左上半场、看向网对面的对手；
- 四人的脚、躯干、头部、球拍也随各自球队朝向一起旋转。每人仍只有一只球拍，两手两脚、自然准备姿势。

必须完整保留当前球场已画出的白线结构，只改变它们随整体旋转后的透视：
两条前发球线必须都在、分别在球网两侧，平行于球网并横贯整场宽度；每半场中线从底线到前发球线就停止；两条后发球线、两条内侧单打边线、外边框完整。不要漏掉一侧前发球线，不要把前发球线当成网顶。整套标线是同一张标准场地图的透视变换。

外部建筑、窗、植物、右侧球拍架、整个平台的轮廓与位置完全不动。其他三个运动场、两个聊天群、中央调度岛、六条直连道路、背景留白和1672x941整图均不动。只在现有羽毛球台座内部旋转并适配完整场地与四人，继续保持原来的精致3D微缩建筑风格。
```

### Mobile composition

```text
Create a separate PORTRAIT 9:16 mobile login background (about 941x1672) by recomposing the first supplied illustration. Use its premium soft 3D architectural miniatures, pale icy blue background, warm oak, teal connecting walkways and natural people. Reference image 2 supplies the correct badminton floor-line topology. No text, labels, logos, phone frame, login form or watermark.

Leave top 0–14% height completely blank for real HTML title. Leave bottom 67–100% completely blank for real HTML login form. ALL illustrations stay in the middle 17–63% height, x=3–97%, with clear breathing room.

Exactly SEVEN islands:
One central circular management desk with calendar and one seated person, at center x50%, y40%.
Four SPORTS venues: badminton lower left and largest focal point (x5–58%,y44–62%), table tennis upper left (x5–40%,y25–38%), basketball half-court upper middle (x30–65%,y18–31%), gym upper right (x67–96%,y25–37%).
Exactly TWO separate CHAT GROUP islands to the right: one at x73–96%,y41–49%, another at x66–94%,y53–62%; each a friendly small robot, two seated adults and one upright abstract chat card with blank graphical marks.
Crucial topology: exactly SIX distinct independent teal walkways radiate DIRECTLY FROM THE SINGLE CENTRAL HUB, one to each of the four sports and two chat groups. No venue-to-venue links, no chained branches. Readable star composition, paths don't cross a court or another island.

Badminton: copy the regulation layout from reference 2 onto a convincing perspective rectangular teal floor, 13.4:6.1 ratio, long axis upper-left to lower-right. One net crosses the SHORT width in / diagonal direction. Include both short service lines 1.98m each side of net, both doubles rear service lines 0.76m inside each baseline, inner singles sidelines 0.46m inside outer sidelines, and center service dividers that STOP at short service lines. Both front service areas are clear of center dividers. Make both short service lines visible from the camera. FOUR natural adult doubles players, blue/yellow on upper-left half BOTH facing lower-right ACROSS net, two white shirts on lower-right half BOTH facing upper-left ACROSS net. In particular foreground white-shirt man's racket is on the LEFT of his body reaching toward the net, and blue-shirt man's racket is on the RIGHT pointing toward opponents. Opponents face one another, teammates don't face one another. One racket each, two arms/two legs each, restrained ready poses, one shuttle only. No distorted anatomy.
Table tennis: rectangular table divided by net at midpoint, white perimeter and one longitudinal centerline, two players at opposite ENDS, one paddle each.
Basketball half-court: one hoop at baseline, correctly centered rectangular key/free-throw line and semicircle, one three-point arc with straight corner segments, center half-circle at open baseline. Two players, one ball, not arbitrary overlapping arcs.
Gym: stationary bike with correctly seated rider, dumbbell rack and a bench, no court lines.
Keep the first image's friendly calm rendering and coherent architectural scale. Preserve the distinct sports, chat groups and one central hub. Not a dashboard screenshot.
```

### Mobile court alignment

```text
Use case: precise-object-edit.
Image 1 is the mobile portrait EDIT TARGET (941 x 1672). Image 2 is the corrected desktop REFERENCE for the badminton court only.
Replace only the badminton floor, white court markings, net and four players inside the lower-left badminton building of image 1 with the correctly aligned badminton arrangement shown in image 2. Keep image 1's exterior building, footprint, all other venues, all six separate hub connections, both chat groups, central calendar island, top and bottom pale-blue blank spaces exactly in place. Output must stay portrait 941 x 1672.

Critical geometry: the court, markings, net and all four people are ONE oriented scene. Long sidelines run upper-left to lower-right, parallel to the long planted wood wall of the existing building. Baselines and net run lower-left to upper-right, across that long axis. Do NOT make a front-facing vertical court inside the diagonal building.
Two people occupy the upper-left half facing lower-right toward their opponents; the other two occupy the lower-right half facing upper-left. Use the exact coherent four-player orientation of image 2. Four people total, one racket per person, two arms and two legs each, natural ready positions.
Keep every court marking visible: outer doubles rectangle, two inset singles sidelines, two rear doubles-service lines, TWO front short-service lines (one on each side of the net, parallel to the net, each spanning the court width), and a center service divider on each half that stops at the short-service line. Do not omit either short service line. These are all lines on one plane with the same perspective.
Keep soft polished miniature 3D illustration quality and original colors. No labels, annotations, red circles, extra rackets, text or watermark. Only the badminton interior changes.
```

### Mobile doubles detail correction

```text
局部修复这张手机竖图，其他区域严格不变。现在羽毛球场只有三名运动员，漏了蓝衣运动员的搭档。请只做下面两处修正：
1. 在球网上方的远端半场补上一名白色运动上衣、深色短裤的运动员，与蓝衣运动员同队。新增人物大约在蓝衣人物右上方的空位，头部约(278,883)、双脚约(263,950)与(306,944)（整图941x1672坐标，仅作定位）。必须站在球网上方那半场的蓝色地板内，不站到墙上。自然屈膝接球，脸、躯干、双脚都朝屏幕右下方，面向网对面的黄衣和白衣对手。新增人物只有一支球拍，两臂两腿。现有三个人不动。最终远端蓝+白两人、近端黄+白两人，共四人。
2. 补出远端半场缺失的白色发球区中线：从远端底线中点沿长轴向右下方延伸，直到远端前发球线停止（可被蓝衣人物局部遮挡），与近端已有的中线对应。现有所有其他白线，尤其球网两边的两条前发球线完整保留。
球场的整体方向已经正确，不再旋转球场或改变建筑。球网、四条边、所有场地、中央岛、六条直连道路、两群、留白都保持。画质和光影保持一致。
```

### Mobile net corridor correction

```text
仅精修手机竖图羽毛球场的一条错误白色中线。所有四名人物、朝向、球网、建筑、其他白线与整图布局完全不动。
远端半场的发球区中线应从远端底线中点开始，一直到远端前发球线结束，不可以从前发球线继续画到球网。整图941x1672里，正确线段大约是(143,899)到(229,965)，可被蓝衣运动员局部遮挡。把这条细白线接到底线。
现在错误地延长进网前区域的白线，大约(229,965)到(279,1011)，应擦掉并还原蓝色地面；原有球网经过这段时必须完整保留，不能擦掉球网。近端中线从近端前发球线之后才出现。
保留球网两侧、平行球网的两条横贯场宽的前发球线。不要改变其余线、球场方向、四个人的位置姿态和物品。不要添加任何新物体。原样保持941x1672完整竖图、色彩、柔和光影与清晰度。
```

### Mobile service divider correction

```text
只给图中羽毛球场补齐两条白色发球区中线，精确局部编辑，不要改其他内容。当前右下方近端半场完全缺少中线，左上远端中线也只画了半截。
在941×1672图像中：
A. 补齐左上半场细白直线，从远端底线中点(143,899)延伸到远端前发球线(229,965)。蓝衣选手遮挡部分不必显示，但其余段必须连续。
B. 补上右下半场细白直线，从近端前发球线中点(279,1011)延伸到底线中点(383,1101)。这条线应清楚可见，从黄衣与白衣两人的中间穿过地面，只是地面白线。
两条线均沿球场的左上↘右下长轴，与球场长边平行。中央球网前后发球线之间的走廊保留纯蓝地面、不画纵线。不能删除任何原有横向发球线和边线，不改变四个人、球拍、球网、建筑、透视、色彩、六个连接、留白或任何其他区域。这次只新增和延长这两条地面白线。
```

### Mobile near-court divider

```text
只改羽毛球场右下半场的一处地面标线：黄衣人和白衣人所在这一半场，还缺一条将左右发球区分开的白色中线。请清晰画出这条白线，从他俩中间、靠近球网那条前发球线的中点，向右下延伸到后端底线的中点，把近端发球区分成左右两个矩形。线在两个人之间的蓝色地面上，走向与两条长边平行；不要从球网开始画，而是从近端前发球线开始。这是唯一要增加的线，其他所有像素内容、人物、场地朝向、现有标线、球网和整张941x1672竖图构图保持原样。务必让近端半场中间能看见这条完整的白色分隔线。
```
