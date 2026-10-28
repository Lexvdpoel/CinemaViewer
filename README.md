# CinemaViewer

Fullscreen bioscoop-applicatie voor Windows en Linux Mint.

- **Bedieningsscherm** (instelbaar): mediabibliotheken, posters, details, voortgang van het afspelen,
  eindtijd, volume, audio-/ondertitelsporen, discmenu en technische media-informatie.
- **Projectiescherm** (instelbaar): toont de film. Als er niets speelt staat hier de placeholder
  “CinemaViewer – by L. van der Poel”.
- **Overige schermen**: de placeholder, en tijdens het afspelen optioneel een “Nu te zien”-kaart
  met poster, titel, eindtijd en een klein live-voorbeeld van de film. Het voorbeeld is een verkleinde
  kopie (±12 beelden/s) van wat er op het projectiescherm staat, dus ook DVD-menu’s zijn zichtbaar.
  Met het voorbeeld aan staat de kaart in drie kolommen: poster · titel en info · voorbeeld met voortgangsbalk.
- Bij meer dan één scherm blijven de publieksschermen altijd bovenop, zodat meldingen of andere
  programma’s nooit op de projector (of in het voorbeeld) verschijnen.
- De muisaanwijzer blijft op het bedieningsscherm (op de publieksschermen is hij onzichtbaar en raak je
  hem anders kwijt). Dat geldt alleen zolang CinemaViewer het actieve programma is; uit te zetten onder
  Instellingen → Schermen.

Bronnen: **Jellyfin**, **Plex**, **lokale mappen / netwerkshares**, **USB-schijven** (automatisch herkend)
en **DVD / Blu-ray / audio-cd** in het disc-station (en DVD/Blu-ray-mappen of .iso-bestanden), plus
**internetradio**, een **browser** die je op de beamer zet en **presentaties** (PowerPoint, Impress, PDF).

## Vereisten

**Windows 10 of 11 (64-bit)**, of **Linux Mint 22** (zie *Linux Mint (kiosk)* hieronder). Onder Windows zit alles wat
CinemaViewer nodig heeft in de map `vendor` (bij elke GitHub-release als `CinemaViewer-vendor-<versie>.zip`):

| Onderdeel | Bestand |
| --- | --- |
| Python 3.14 (embeddable) | `vendor\python-3.14.7-embed-amd64.zip` |
| PySide6 (Qt, met WebEngine en Pdf), python-vlc, yt-dlp | `vendor\wheels\*.whl` (inclusief Microsoft C++-runtime) |
| VLC 3.0.24 (libVLC + plugins, ook DVD/Blu-ray) | `vendor\vlc-3.0.24-win64.zip` |

Er hoeft dus niets geïnstalleerd te worden en er is geen internet nodig. Alleen voor **presentaties** moet
[LibreOffice](https://www.libreoffice.org) (gratis) geïnstalleerd zijn; op de Linux-kiosk zit het er al in.

## Installeren op een andere pc

1. Kopieer de map `CinemaViewer` naar de nieuwe pc, bij voorkeur op een kort pad zoals `C:\CinemaViewer`.
   De mappen `.venv` en `runtime` hoef je niet mee te kopiëren.
2. Dubbelklik op `run.bat`.

Bij de eerste start pakt `run.bat` (via `tools\setup.ps1`) alles uit naar de map `runtime` (±350 MB,
ongeveer 20 seconden) en start daarna CinemaViewer. Bij volgende starts gebeurt dat niet meer.
Kopieer je de map mét `runtime`, dan start hij meteen. Opnieuw installeren: verwijder `runtime\ready.txt`.

Instellingen staan per pc in `%APPDATA%\CinemaViewer\config.json`; kopieer dat bestand mee als je
dezelfde bronnen en schermen wilt gebruiken. Daarna eenmalig *Rechten instellen* (Systeem) en eventueel
*Automatisch starten* aanzetten.

Handmatig starten: `runtime\python\python.exe -m cinemaviewer` (met `--windowed` voor testen in vensters).

Onderdelen bijwerken (ontwikkelaar): nieuwe wheels met `pip download --only-binary=:all: --platform win_amd64
--python-version 3.14 --no-deps -d vendor\wheels PySide6_Essentials shiboken6 python-vlc`, VLC opnieuw
inpakken met `python tools\pack_vlc.py`, daarna `runtime\ready.txt` verwijderen.

Automatisch starten met Windows: Instellingen → *Systeem* → “CinemaViewer automatisch starten”.
CinemaViewer start dan zodra deze Windows-gebruiker is aangemeld (zonder consolevenster, via
`CinemaViewer.pyw`). Zet voor een echte bioscoop-pc ook automatisch aanmelden aan (Microsoft Autologon
of `netplwiz`), dan verschijnt na het aanzetten direct de bioscoop. CinemaViewer draait maar één keer tegelijk.
Bij zo'n automatische start blokkeert CinemaViewer de muis totdat het programma klaar is met opstarten
(na hooguit 90 seconden wordt de muis altijd weer vrijgegeven). Start je CinemaViewer zelf, of staat
automatisch starten uit, dan blijft de muis gewoon werken.

**Bioscoop-pc** (Instellingen → *Systeem* → “CinemaViewer vervangt het Windows-bureaublad”): Windows start
na het aanmelden CinemaViewer in plaats van de Verkenner, dus er is geen startmenu, taakbalk of bureaublad.
Dit geldt alleen voor deze Windows-gebruiker en vanaf de volgende keer aanmelden. Een klein bewakingsproces
(`cinemaviewer\shell.py`) start CinemaViewer:
- *Naar admin omgeving* start daarna het gewone bureaublad (ook pas dan starten andere programma's die met Windows meestarten);
- crasht CinemaViewer, dan start het opnieuw; na drie crashes binnen twee minuten worden de
  Ctrl+Alt+Del-beperkingen opgeheven en verschijnt het bureaublad;
- **noodroute:** houd **Shift** ingedrukt direct na het aanmelden, dan start meteen het gewone bureaublad.

Handmatig uitzetten (bijvoorbeeld vanuit een ander account of na de noodroute):
`reg delete "HKCU\Software\Microsoft\Windows NT\CurrentVersion\Winlogon" /v Shell /f`

## Afsluiten

De aan/uit-knop rechtsboven (of Ctrl+Q) opent een keuzemenu:

- **Naar admin omgeving** – sluit CinemaViewer en toont het bureaublad; vraagt het instellingenwachtwoord.
- **Computer uitschakelen** – sluit CinemaViewer en zet de pc uit.

Alt+F4 wordt genegeerd. Met de optie *Systeem → Windows-toets, Alt+Tab en het Ctrl+Alt+Del-menu blokkeren*
zijn ook de Windows-toets, Alt+Tab, Alt+Esc, Ctrl+Esc en Ctrl+Shift+Esc geblokkeerd zolang CinemaViewer draait.
Ctrl+Alt+Del kan door geen enkel programma worden geblokkeerd (Windows-beveiliging),
maar met *Systeem → Ctrl+Alt+Del-menu beperken* zijn daarin Taakbeheer (ook Ctrl+Shift+Esc), Vergrendelen,
Afmelden en Wachtwoord wijzigen uitgeschakeld zolang CinemaViewer draait. Hiervoor is eenmalig
*Rechten instellen* nodig (UAC-vraag): dat geeft jouw gebruiker het recht die vier beleidsinstellingen zelf
aan en uit te zetten. Bij ‘Naar admin omgeving’ en bij uitschakelen worden ze weer opgeheven.
Is CinemaViewer gecrasht en werkt Taakbeheer niet meer? Voer uit:
`runtime\python\python.exe -m cinemaviewer --herstel` (of start CinemaViewer en kies ‘Naar admin omgeving’).

## Achtergrondafbeeldingen

In de map `wallpapers` staan de placeholder-afbeeldingen `CinemaViewer-1920x1080.png` en
`CinemaViewer-1920x1200.png`. Met *Instellingen → Schermen → Achtergrondafbeeldingen opslaan* maak je ze
opnieuw met je eigen titel/ondertitel (plus een versie in de resolutie van je hoofdscherm).

*Placeholder ook gebruiken als Windows-bureaubladachtergrond* maakt de afbeelding in de resolutie van het
hoofdscherm en zet hem als bureaublad (op alle schermen, ‘opvullen’). Verander je de teksten of de
resolutie, dan wordt hij vanzelf bijgewerkt. Zet je de optie uit, dan komt je vorige afbeelding terug;
was dat Windows Spotlight, een diavoorstelling of een effen kleur, dan opent CinemaViewer het
Windows-instellingenscherm om die weer aan te zetten (dat kan een programma niet zelf).

Het **opstartlogo** (het logo met het draaiende bolletje bij het aanzetten) is geen Windows-instelling: dat
komt uit de firmware (UEFI/BIOS) van het moederbord. Het is daarom niet vanuit CinemaViewer aan te passen.

## Taakbalk verbergen (Instellingen → Systeem → Opstarten)

*Windows-taakbalk verbergen, ook tijdens het opstarten* zet de Windows-instelling ‘taakbalk automatisch
verbergen’ aan, zodat de taakbalk bij het opstarten al weg is, nog vóór CinemaViewer start. Zolang
CinemaViewer draait, is hij op alle schermen helemaal verborgen (ook niet als de muis de schermrand raakt).
Bij ‘Naar admin omgeving’ verschijnt hij weer zodra je de muis naar de schermrand beweegt. Zet je de optie uit,
dan komt je eigen taakbalkinstelling terug.

## Windows-aanmelding (Instellingen → Systeem)

- **Vergrendel- en aanmeldscherm: placeholder-achtergrond (zonder tekst)** – het podium met de gordijnen,
  zonder tekst, als achtergrond van het vergrendel- en aanmeldscherm. De afbeelding staat in
  `C:\ProgramData\CinemaViewer\vergrendelscherm.png` en volgt de resolutie van het hoofdscherm.
- **Automatisch aanmelden als <gebruiker>** – Windows meldt de huidige gebruiker bij het opstarten
  automatisch aan, zodat (met *Automatisch starten*) direct de bioscoop verschijnt. CinemaViewer vraagt
  eenmalig het Windows-wachtwoord (bij een Microsoft-account het wachtwoord van dat account, niet de pincode),
  controleert het en laat Windows het versleuteld bewaren als LSA-geheim, zoals Microsofts Autologon doet.
  Het staat nooit leesbaar in het register. Uitzetten wist het geheim weer.

Beide opties vragen bij het wijzigen één keer om beheerdersrechten (UAC).

## Pauze

De knop **Pauze** in de bedieningsbalk (of toets P) zet de film stil en toont op alle publieksschermen
“Pauze”, met de tijd waarop de film verdergaat. Na de ingestelde pauzeduur, of eerder met **Film hervatten**
(of spatie), volgt een aftelling van 10 seconden en gaat de film 15 seconden vóór het pauzemoment verder.
Duur, aftelling en terugspoelen stel je in onder *Instellingen → Pauze* (duur 0 = tot je handmatig hervat).

### Geplande pauze halverwege

Zet het vinkje **Pauze halverwege inplannen** (bij Afspelen in Details) of **Halverwege** (naast de
Pauze-knop) aan; de keuze wordt onthouden. CinemaViewer start de pauze dan zelf halverwege de film,
gerekend tot het begin van de aftiteling:

1. Begin van de aftiteling: de markering van Jellyfin (media segment ‘Outro’) of Plex (credits-marker),
   anders een hoofdstuk met ‘Credits’/‘Aftiteling’ in de naam, anders een schatting (6% van de speelduur,
   1–10 minuten). Jellyfin maakt zelf geen ‘Outro’-segmenten; daarvoor is een plugin nodig zoals
   *Intro Skipper* (Dashboard → Plugins → Catalogus). Plex doet dit zelf met Plex Pass (credits detection).
2. De pauze valt halverwege tot dat punt, en 3 seconden vóór een hoofdstukwissel als die er vlakbij ligt
   (hoofdstukken uit het bestand, de DVD/Blu-ray of van Jellyfin/Plex), zodat de pauze tussen twee scènes valt.

Het moment staat als gouden streep op de voortgangsbalk en onder *Nu speelt → Geplande pauze* (met de
verwachte kloktijd). Niet bij films korter dan 20 minuten, niet als je de film hervat ná het pauzemoment,
en niet nog eens als je zelf al rond het midden op Pauze hebt gedrukt.

## Projector en receiver (Instellingen → Apparaten)

CinemaViewer zet de receiver aan bij het starten en zet beide apparaten uit bij het afsluiten (‘Naar
Windows’ én ‘Computer uitschakelen’). De **beamer** gaat bij het starten níet vanzelf aan, zodat alleen
muziek luisteren geen lampuren kost: staat hij uit, dan verschijnt bovenin de knop **Beamer aan**. Tijdens
opwarmen en afkoelen toont de knop dat; staat hij aan, dan zet **Beamer uit** hem (na een bevestiging) uit.
CinemaViewer vraagt de beamer regelmatig naar zijn toestand, dus ook aan/uit met de afstandsbediening
wordt gevolgd. Start je een film terwijl de beamer uit staat, dan volgt een melding. Wil je hem toch bij
elke start aan: *Ook automatisch aanzetten bij het starten van CinemaViewer* (Instellingen → Apparaten).

- **Projector**, met drie soorten besturing:
  - *PJLink* (TCP-poort 4352) voor projectoren die dat kennen. Zet in het projectormenu netwerk-/PJLink-
    besturing aan en stand-by-stroom op *netwerk* (niet eco). Staat PJLink-beveiliging aan, vul dan het
    PJLink-wachtwoord in.
  - *InFocus via netwerk* (telnet, poort 23) voor InFocus-projectoren zonder PJLink, zoals de IN5535. Zet in
    het menu *Setup → Always-On Functions → Network* op *Yes*, geef hem een vast IP-adres en zet op zijn
    webpagina (inloggen als Administrator, *Network Settings*) *Command and Control Pass-Through* aan.
  - *InFocus via RS-232* met een USB-naar-serieel-kabel; de snelheid wordt automatisch gevonden.

  Bij InFocus leest *Verbinding testen / status opvragen* de status, storingen en voor **beide lampen** de
  branduren en of ze branden. Optioneel zet CinemaViewer bij het aanzetten de lampmodus (beide lampen of
  één) en het lampvermogen (normaal/eco). Zolang CinemaViewer draait, controleert hij elke minuut de
  projector en meldt hij één keer als een lamp niet brandt terwijl beide lampen aan horen te staan, als de
  projector een storing meldt, of als een lamp het ingestelde aantal branduren (standaard 2000) bereikt.
- **Receiver** (bv. Denon AVR-4311) via het Denon-protocol (telnet, poort 23): aan (`PWON`), ingang
  (`SI…`, bv. BD, DVD, SAT/CBL) en uit naar stand-by. Zet op de receiver *Network Standby* aan.
  Met **volumeregeling** speelt CinemaViewer zelf op vol niveau en regelen de volumeschuif en dempen de
  receiver; 100% = het ingestelde maximum (standaard −10 dB). Volume wijzigen met de afstandsbediening
  of de knop wordt op de schuif overgenomen. De receiver accepteert maar één netwerkverbinding tegelijk:
  gebruik geen andere app (bv. de Denon-app) die de verbinding vasthoudt. Volgens het Denon-protocol
  stuurt CinemaViewer bij het schuiven hooguit 5 volumestappen per seconde (de laatste stand komt altijd
  aan) en controleert hij of de receiver elke wijziging bevestigt. Valt de verbinding stil weg, dan wordt
  ze opnieuw opgebouwd en de opdracht nogmaals verstuurd; bij stilte wordt elke 30 seconden gecontroleerd
  of de verbinding nog leeft.

Bij het afsluiten wordt elk apparaat tot drie keer geprobeerd uit te zetten (de receiver moet stand-by
bevestigen). Reageert de beamer en/of receiver dan nog niet, bijvoorbeeld omdat de beamer nog aan het
opwarmen is, dan vraagt CinemaViewer *“Toch afsluiten?”*. Kies je *Nee*, dan blijft CinemaViewer open en
worden de apparaten weer aangezet.

Geef beide apparaten een vast IP-adres (of een DHCP-reservering in de router).

## Instellingen

Via het tandwiel rechtsboven (of Ctrl+,). Opgeslagen in `%APPDATA%\CinemaViewer\config.json`.

De instellingen zijn beveiligd met een wachtwoord (standaard `1a2b3c4d5e`). Hetzelfde wachtwoord
beveiligt ‘Naar admin omgeving’ bij afsluiten. Wijzig het onder *Systeem*; alleen een versleutelde hash wordt opgeslagen. Wachtwoord vergeten? Sluit CinemaViewer,
wis de waarde van `settings_password_hash` in `config.json` (maak er `""` van), dan geldt weer het standaardwachtwoord.

| Tab | Wat |
| --- | --- |
| Schermen | Bedienings- en projectiescherm kiezen (“Schermen identificeren” toont een nummer op elk scherm, “Beeldscherminstellingen openen” opent die van Windows/Linux), placeholdertekst, “Nu te zien”, voortgang terugmelden |
| Uitgangen | Uitgangen forceren met vaste resolutie/verversing (bv. HDBaseT zonder EDID) en de geluidsuitgang van films kiezen (onder Linux ook “HDMI forceren”) |
| Jellyfin | Server-adres + gebruikersnaam/wachtwoord → *Verbinden*. Alleen het token wordt opgeslagen, niet het wachtwoord |
| Plex | Server-adres + X-Plex-Token → *Verbinding testen* |
| Lokaal, USB & disc | Mediamappen toevoegen; USB- en disc-stations aan/uit |
| Radio | Eigen lijst met radiozenders; zoeken per land via Radio Browser |
| Pauze | Pauzeduur, aftelling en terugspoelen |
| Apparaten | Projector (PJLink of InFocus via netwerk/RS-232, met lampstatus) en Denon-receiver automatisch aan/uit, ingang en volumeregeling → *Verbinding testen* |
| Systeem | Automatisch starten met Windows; Ctrl+Alt+Del-menu beperken; wachtwoord wijzigen |

Met **één scherm** gaan film en bediening op hetzelfde scherm: **Tab** of dubbelklik wisselt.
Film op **meerdere projectoren** tegelijk: zet die schermen in Windows op *Dupliceren*.

**Uitgangen forceren** (tab *Uitgangen*): geeft een extender (HDBaseT, matrix) de schermgegevens (EDID) niet
of te laat door, dan ziet het systeem geen scherm. CinemaViewer zet de gekozen uitgang bij het starten zelf
aan met de opgegeven resolutie (onder Linux via `xrandr`, alleen in een X11-sessie; zo nodig met een eigen
modeline via `cvt`) en probeert het tot 40 seconden na het starten opnieuw. *Nu toepassen* test het direct.
Windows kan een uitgang waarop het helemaal geen scherm ziet niet aanzetten; daar helpt forceren vooral
bij een verkeerde of te late resolutie. Bij **Geluidsuitgang** kies je waar het filmgeluid heen gaat; onder
Linux zet *HDMI forceren* het HDMI-profiel van de geluidskaart aan, ook als PipeWire/PulseAudio de uitgang
als ‘niet beschikbaar’ toont.

In muziek (afspeellijst, artiest, album) speelt **Alles afspelen** (bovenbalk) alle titels achter elkaar af via de
wachtrij; met een zoekterm alleen de gevonden titels. Dubbelklik in een afspeellijst of album speelt vanaf
dat nummer de rest van de lijst af.
**Willekeurig** (naast *Alles afspelen*) speelt doorlopend willekeurige nummers uit de lijst, tot je op
Stoppen of *Wis alles* drukt of zelf een andere titel kiest. Een nummer komt hooguit twee keer per uur
langs en nooit twee keer achter elkaar; nummers die het afgelopen uur nog niet speelden gaan voor. Het
volgende nummer staat steeds alvast in de wachtrij (en als ‘Hierna’ op het muziekscherm). Is in een kleine
lijst elk nummer het afgelopen uur al twee keer gespeeld, dan stopt willekeurig afspelen na het huidige nummer.

## Bediening

| Toets | Actie |
| --- | --- |
| Spatie / K / mediatoets | Afspelen / pauze |
| Ctrl+S / Stop-mediatoets | Stoppen |
| Ctrl+← / J | 10 s terug; bij muziek vorig nummer (na 3 s eerst terug naar het begin) |
| Ctrl+→ / L | 30 s vooruit; bij muziek volgend nummer |
| + / − (of Ctrl+↑ / Ctrl+↓) | Volume |
| M | Geluid aan/uit |
| P | Pauze starten / film hervatten |
| Backspace | Map terug |
| Tab | (één scherm) wissel film ↔ bediening |
| Ctrl+, | Instellingen |
| Ctrl+Q | Afsluitmenu (naar admin omgeving / computer uit) |

Klik op een poster voor details (met *Hervatten* als je eerder gestopt bent), dubbelklik om direct af te spelen.
Voor DVD/Blu-ray verschijnt in de bedieningsbalk een disc-knop met menunavigatie en hoofdstukken.

## Lokale metadata

Voor bestanden in mappen leest CinemaViewer (indien aanwezig) Kodi-stijl metadata:
`poster.jpg` / `folder.jpg` / `<film>-poster.jpg`, `fanart.jpg` en `<film>.nfo` / `movie.nfo`
(titel, jaar, plot, genres, leeftijdsclassificatie). Zonder metadata wordt de titel netjes uit de
bestandsnaam gehaald (`The.Matrix.1999.1080p.BluRay.mkv` → *The Matrix (1999)*).

Muziekbestanden (mp3, flac, m4a, wav, ogg, …) in mappen worden ook getoond. De hoes komt uit
`cover.jpg` / `folder.jpg` / `front.jpg` in de albummap, anders uit de hoes die in het bestand zelf zit;
artiest en album uit de tags. `fanart.jpg` in de album- of artiestmap wordt de achtergrond.

## Displaycast (YouTube en DLNA vanaf telefoons)

Klik links op **Displaycast**: CinemaViewer wordt dan op het netwerk zichtbaar als ontvanger (standaard
‘CinemaViewer (computernaam)’, aan te passen onder Instellingen → Apparaten).

- **YouTube** – CinemaViewer gedraagt zich als een YouTube-tv-app. Tik in de YouTube-app op het cast-icoon
  en kies CinemaViewer (zelfde netwerk), of koppel via *Instellingen → Tv bekijken → Koppelen met tv-code*
  met de code die in het Displaycast-paneel staat. De telefoon wordt een afstandsbediening: video's en de
  wachtrij kies je daar, pauze, spoelen en volume werken in beide richtingen. Gekoppelde telefoons blijven
  gekoppeld. De video's worden in maximaal 1080p afgespeeld via **yt-dlp**; werkt YouTube ineens niet meer,
  dan haalt CinemaViewer zelf de nieuwste yt-dlp op (of gebruik *yt-dlp bijwerken* in het paneel).
  Dit gebruikt YouTube's eigen (ongedocumenteerde) tv-protocol, gebaseerd op het open-source project
  yt-cast-receiver; YouTube kan dat ooit veranderen.
- **DLNA / UPnP** – apps die naar een ‘mediaspeler’ of DLNA-tv kunnen sturen (BubbleUPnP, Web Video Cast,
  VLC, galerij-apps, ‘Casten naar apparaat’ in Windows) sturen foto's, video's en muziek, die CinemaViewer
  met de gewone speler afspeelt (muziek op het muziekscherm), met bediening en voortgang terug naar de app.

Wat een telefoon stuurt, vervangt direct wat er speelt. Een telefoon kan een film die hier is gestart niet
pauzeren of stoppen. **Netflix, Prime Video** en andere beveiligde diensten werken niet: die vereisen een
door Google gecertificeerde Chromecast of de app op een smart-tv.

Windows vraagt de eerste keer om netwerktoegang voor Python: kies *Toestaan* (privénetwerk), of gebruik
*Toegang in Windows Firewall instellen* in het paneel. Displaycast gebruikt UDP-poort 1900 (zoeken) en
TCP-poort 47801. Met *Displaycast meteen starten wanneer CinemaViewer start* (Instellingen → Apparaten) is
het altijd beschikbaar; anders vanaf het moment dat je het opent, tot je op *Stoppen* klikt.

## Radio

Links onder **Radio → Radiozenders** staan internetradiozenders; dubbelklik om er een af te spelen. Het
projectiescherm toont dan het logo, het nummer dat de zender doorgeeft (artiest en titel), de zendernaam
en een LIVE-label. Standaard staat er een lijst met 19 Nederlandse zenders klaar (NPO Radio 1/2/5, 3FM,
Klassiek, Soul & Jazz, FunX, 538, Qmusic, Sky Radio, Radio 10, Veronica, 100% NL, SLAM!, KINK, JOE, Arrow
Classic Rock, BNR en Sublime).

Onder **Instellingen → Radio** pas je die lijst aan: volgorde, verwijderen, een eigen stream-adres
toevoegen, of zoeken in [Radio Browser](https://www.radio-browser.info), een open, door gebruikers
bijgehouden lijst van meer dan 50.000 zenders per land. Kies een land, zoek op naam (of laat het veld leeg
voor de populairste zenders) en voeg toe. *Standaardlijst (Nederland)* zet de lijst terug. Bij het afspelen
vraagt CinemaViewer Radio Browser om het actuele stream-adres van de zender; is de dienst niet bereikbaar,
dan wordt het opgeslagen adres gebruikt.

## Muziek op het projectiescherm

Bij muziek (en elk ander bestand zonder beeld) toont het projectiescherm geen zwart beeld maar een
muziekscherm: de albumhoes met titel, artiest, album, voortgang en wat er hierna komt, op een vervaagde
achtergrond van de hoes (of de artiestfoto) binnen de gordijnen. Langs de onderkant beweegt een
gespiegeld spectrum mee met de muziek en de gloed rond de hoes ademt mee met de bas. Zonder hoes
verschijnt een grammofoonplaat in de huisstijl.

## Bibliotheek (offline afspelen)

Films en afleveringen uit **Plex** en **Jellyfin** download je met *Naar bibliotheek downloaden* (Details). Bestanden
uit **mappen, netwerkshares en USB-schijven** en **onbeveiligde DVD's/Blu-rays** (eigen opnames, of een DVD-/Blu-ray-
map) kopieer je met *Naar bibliotheek kopiëren*; je geeft dan aan of het een film of een aflevering is (CinemaViewer
raadt het al uit de naam, bv. `S01E02`). Op schijf staat de bibliotheek ingedeeld:

```
Films/<Titel (jaar)>/
Series/<Serie>/Seizoen <n>/<S01E02 Titel>/
Muziek/<Artiest>/<Album>/<Titel>/
```

(Windows: `%LOCALAPPDATA%\CinemaViewer\cache`, Linux: `~/.local/share/CinemaViewer/cache`; oudere downloads worden
bij de eerste start vanzelf zo ingedeeld.) Links onder *Bibliotheek* staan *Films* en *Series*, met daaronder de vrije
ruimte. Een film of aflevering die in CinemaViewer tot de aftiteling (90%) is bekeken, krijgt een vinkje;
**Gekeken verwijderen** (bovenbalk in de bibliotheek, en links) ruimt die in één keer op. Per item verwijder je met
*Uit bibliotheek verwijderen* (Details). Wat nu speelt, wordt nooit verwijderd.

Kopieerbeveiligde discs (CSS-DVD's, AACS-Blu-rays) worden niet gekopieerd: CinemaViewer omzeilt geen
kopieerbeveiliging. Afspelen vanaf de disc kan gewoon.

Klik op een kopje links (JELLYFIN, PLEX, BIBLIOTHEEK, MAPPEN, ...) voor het overzicht van alles daaronder.

## Netwerkshares (Instellingen → Lokaal, USB & disc)

Voeg een **SMB**- (Windows-share, NAS) of **NFS**-share toe met server, share, en zo nodig gebruikersnaam en
wachtwoord; hij verschijnt links onder *Mappen*.

- **Windows**: de share wordt direct gebruikt als `\\server\share`; gebruikersnaam en wachtwoord bewaart Windows in
  Referentiebeheer (`cmdkey`), niet in `config.json`. Voor NFS moet de Windows-functie *Client voor NFS* aan staan.
- **Linux**: bij Opslaan wordt één keer om het beheerderswachtwoord gevraagd. De share komt in `/etc/fstab` (tussen
  `# BEGIN CinemaViewer` en `# END CinemaViewer`) met systemd-automount onder `/mnt/cinemaviewer/<naam>`, dus hij
  koppelt bij elke start en na een netwerkstoring vanzelf. Het wachtwoord staat in
  `/etc/cinemaviewer/shares/<naam>.cred`, alleen leesbaar voor root.

## Updates via GitHub (Instellingen → Systeem → Updates)

Vul de repository in (`eigenaar/naam`) en, voor een privé-repository, een *fine-grained token* met alleen
*Contents: Read-only* voor die repository. Bij het starten kijkt CinemaViewer of er een nieuwere **release** is en
meldt dat; *Bijwerken naar …* downloadt hem, vervangt alleen het programma (instellingen, bibliotheek en `runtime`
blijven) en start CinemaViewer opnieuw. Tijdens het afspelen wordt niet bijgewerkt.

Een nieuwe versie uitbrengen (ontwikkelaar): verhoog `__version__` in `cinemaviewer/__init__.py`, commit, en voer
`tools\release.ps1` uit; dat maakt de tag, de GitHub-release met de wijzigingen en hangt de vendor-zip eraan.

## Browser

Kies links **Browser**. In het midden van het bedieningsscherm staat een webbrowser met adresbalk (webadres of
zoekterm), terug/vooruit/vernieuwen en een startpagina (Instellingen → Systeem → Browser). De pagina is opgemaakt
zoals hij op de beamer verschijnt, alleen verkleind. **Naar beamer** zet de browser schermvullend en in volle
resolutie op de beamer (video in de pagina speelt daar vloeiend); op het bedieningsscherm zie je dan een live
voorbeeld waarin je gewoon klikt, scrolt en typt. Dezelfde knop (**Stop beamer**), de stopknop of het starten van een
film haalt hem weer weg; video's in de pagina worden dan gepauzeerd. Downloaden en het openen van bestanden of andere
programma's vanuit de browser kan niet; inloggegevens van websites worden bewaard.

## Presentaties

PowerPoint- (`.pptx`, `.ppt`, `.ppsx`, `.pps`, `.potx`, `.pptm`), Impress- (`.odp`) en PDF-bestanden staan tussen de
films op USB-sticks, cd/dvd's, mappen en netwerkshares. Dubbelklik (of **Presentatie op de beamer**) en de beamer
toont een echte **LibreOffice-diavoorstelling**, met animaties, overgangen en ingesloten video. Er is geen Microsoft-
software voor nodig.

Op het bedieningsscherm staat dan de **presentatieweergave**, zoals in PowerPoint: de dia op de beamer groot, de
volgende dia, de **notities** van de huidige dia (tekst groter/kleiner met A−/A+), de verstreken tijd (pauzeren,
opnieuw) en de klok, en onderaan alle dia's om naar te springen. Bediening:

| Toets | Actie |
| --- | --- |
| PgDn, →, ↓, N, spatie | volgende animatie of dia (ook de knop van een presentatie-afstandsbediening) |
| PgUp, ←, ↑ | vorige |
| Home / End | eerste / laatste dia |
| B of . | beamer zwart (nogmaals: terug) |

Tijdens een presentatie staat links bovenaan *PRESENTATIE*: klik erop om terug te gaan naar de presentatieweergave.
**Presentatie sluiten** of de stopknop beëindigt hem. De eerste keer openen duurt even (LibreOffice maakt de
voorvertoning en leest de notities); daarna gaat het direct. Verborgen dia's worden overgeslagen. Een PDF heeft geen
animaties of notities en wordt dia voor dia getoond; dat gebeurt ook als de diavoorstelling niet kan starten.

## Linux Mint (kiosk)

Op Linux Mint 22 (Cinnamon) installeer je met `./setup.sh` en start je met `./run.sh`. Voor een bioscoop-pc:
een eigen LightDM-sessie *CinemaViewer* (Instellingen → Systeem → *CinemaViewer vervangt het bureaublad*) met
automatisch aanmelden. In die sessie draait alleen CinemaViewer, met een kale Openbox als vensterbeheer (geen
randen, menu's of sneltoetsen) en zonder schermbeveiliging; USB-schijven worden vanzelf gekoppeld. *Naar admin omgeving*
start daarin het gewone Cinnamon-bureaublad (na het wachtwoord). Noodroute: houd Shift ingedrukt bij het aanmelden.
NVIDIA-kaarten (bv. GT 520 / GT 710) gebruiken de open-source *nouveau*-driver.

## Opmerkingen

- **Blu-ray**: commerciële (AACS-versleutelde) Blu-rays vereisen `libaacs.dll` en een `KEYDB.cfg`
  bij VLC; DVD’s en onversleutelde Blu-rays werken direct.
- Jellyfin- en Plex-media worden direct (zonder transcoderen) afgespeeld; VLC ondersteunt vrijwel alle formaten.
- Alle verbindingen (Jellyfin, Plex, posters en videostreams) melden zich als `CinemaViewer/1.0`, zodat
  Cloudflare of een andere proxy vóór de server ze niet als bot blokkeert (HTTP 403, foutcode 1010).
- Het scherm/de pc gaat niet in slaapstand zolang CinemaViewer draait.

## Licentie

CinemaViewer is vrije software onder de [GNU General Public License v3.0](LICENSE).
