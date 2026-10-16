# TouchTunes style guide (scout report, 2026-10-16). [published] = in shipped code/copy, [inferred] = from screenshots.

## Logo
Wordmark "TouchTunes", bold geometric sans, white on black (inline SVG on site) [published]. App icon: six purple-to-magenta glowing dots in a right-pointing play triangle on near-black [published image]. No published clear-space rules.

## Colors (Tailwind tokens in site CSS, TouchTunes' own names) [published]
black #000000 bg; midnight #1A092E; honeyflower #40156A; regal #133765; aura #BD35FC (primary purple, hover gradient to #8A2BE2); blaze #FF5722; coral #FF7F50; sunburst #FFC700 (focus ring); ocean #17A5E1 (to #20C6D1); greys #DDDDDD/#8C8B88/#222222/#121212; error #FF100A. Extra gradient stops #7C2992, #F89225, #20C6D1.
App [inferred]: bg #000, cards #1F1F1F, inputs #353535, track #2D2D2D, pins #A848FC, rewards gradient #6084E4->#CB9DEB->#F2E4F7, coin gold #E4C078, Now Playing tints from album art. Gradient text for one hero line (yellow->orange, purple, pink->orange). Dark only, no light theme.

## Type
Site: proxima-nova (Adobe Fonts), weights 400/600/700/800, body 16px, ls .25px, lh 134%; h1 800 weight, tight tracking, lh 92-100% [published]. Free stand-in: Montserrat. Sentence case everywhere ("Play your heart out", "Get the app"); title case only in nav.

## UI conventions [inferred unless noted]
Black canvas, 3-tab bottom bar (Places / Play / Profile), thin monochrome line icons. Cards #1F1F1F, ~16px radius, no border, no shadow; radius scale 12/16/24/40/full [published]. Chips are pills; selected = white fill black text. Site primary button: white fill, black text, 700, 12px radius, hover purple gradient 300ms [published]. Songs: square art, white semibold title, grey artist, kebab right. Credits: dark pill top-right with count + gold coin. Profile: gradient ring avatar, stat tiles, tier name, Monthly Bonus progress bar. Social: emoji reactions, send credits to friends, see who played what [published, Business Wire 2025/2026].

## Voice [published]
Short, second person, imperative, upbeat, sentence case, under 6 words for headers. "Play your heart out", "Be the DJ", "Skip the wait", "Turn music into rewards", "The vibe of your favorite hangout, in your hands", "Your recent plays", "Up Next", "Your song", "Play 4 more songs to earn 1 credit". Nouns: credits, Fast Pass, Song Upgrade, TouchTunes Rewards, Check-ins, Plays.

## Co-branding
No published partner rules. Media kit 2022 is ad units only. Recommendation: never use their wordmark as our masthead; say "Works with TouchTunes" in body copy.

## Tokens for our CSS
--tt-bg:#000000; --tt-surface:#1F1F1F; --tt-surface-2:#2D2D2D; --tt-input:#353535; --tt-midnight:#1A092E; --tt-text:#FFFFFF; --tt-text-2:#DDDDDD; --tt-text-3:#8C8B88; --tt-aura:#BD35FC; --tt-aura-deep:#8A2BE2; --tt-blaze:#FF5722; --tt-coral:#FF7F50; --tt-sunburst:#FFC700; --tt-ocean:#17A5E1; --tt-teal:#20C6D1; --tt-coin:#E4C078; --tt-grad-brand:linear-gradient(90deg,#BD35FC,#8A2BE2); --tt-grad-heat:linear-gradient(90deg,#FFC700,#FF5722); --tt-radius:12px; --tt-radius-card:16px; --tt-radius-pill:999px.
Font stack: "proxima-nova","Montserrat","Inter",-apple-system,"Helvetica Neue",Arial,sans-serif. Base 18px for Brent, with a size control.

Sources: touchtunes.com + its compiled CSS and typekit kit sew1xio; App Store id 378351144 via itunes search API; Google Play com.touchtunes.android; Business Wire 2025-03-18 and 2026-03-17; TT-MediaKit-2022.pdf; work.co news; frog.co case study; barbizmag 2016; upshow.tv blog.
