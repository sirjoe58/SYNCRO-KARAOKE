# SirJoe · Syncro Song (server)

Carichi un brano con la voce e il testo: il server ascolta il cantato, allinea ogni parola al suo istante
esatto (forced alignment con **Whisper + stable-ts**) e restituisce un file karaoke **LRC** con i tempi
parola per parola, più un JSON e un'anteprima con le parole che si illuminano.

## Avvio in locale
Requisiti: Python 3.10+, [ffmpeg](https://ffmpeg.org) installato.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Apri http://127.0.0.1:8000. Alla prima sincronizzazione il modello viene scaricato (qualche centinaio di MB).

Con Docker: `docker build -t sirjoe . && docker run -p 8000:8000 sirjoe`

## Impostazioni utili
- `WHISPER_MODEL`: `tiny`, `base`, `small` (predefinito), `medium`, `large-v3`. Più grande = più preciso ma più lento e più memoria.
- `MAX_MB` in `app/main.py`: dimensione massima del file caricato (60 MB).
- Voce isolata: **Demucs** è incluso (pacchetto `demucs-infer`). L'opzione nella pagina è attiva di default: separa la voce dalla musica prima di allineare, con risultati molto migliori sui brani completi. Su CPU aggiunge da 1 a qualche minuto; al primo uso scarica il suo modello (~80 MB). Non è disponibile nella versione EXE: usa `avvia.bat`.

## Limiti da conoscere
- **Serve la voce cantata.** Una base strumentale o un MIDI non contengono parole da riconoscere: in quel caso
  l'allineamento automatico non è possibile (resta la sincronizzazione stimata/manuale dell'app web).
- Un MIDI va prima convertito in audio (es. FluidSynth con un soundfont) e comunque non ha la voce.
- La precisione cala con cori, voci sovrapposte, testo diverso da quello cantato o lingua sbagliata.
- Su CPU un brano di 4 minuti richiede da pochi secondi (`base`) a qualche minuto (`medium`); con GPU è molto più rapido.

## Pubblicazione
Servono ~2–4 GB di RAM con il modello `small`. Opzioni: un VPS (Hetzner, DigitalOcean), Fly.io, Render o Railway
con il Dockerfile incluso; una GPU solo se prevedi molto traffico. Prima di aprirla al pubblico aggiungi
autenticazione, limiti di utilizzo e, se vuoi venderla, pagamenti (es. Stripe). Ricorda che i brani caricati
possono essere coperti da diritti d'autore: valuta termini d'uso e cancellazione dei file (qui vengono
eliminati subito dopo l'elaborazione).

## Struttura
- `app/main.py` – API `POST /api/align`
- `app/aligner.py` – allineamento, separazione voce, generazione LRC
- `static/index.html` – interfaccia web
- `Dockerfile`, `requirements.txt`

## Windows: avvio con doppio clic ed EXE
- **Avvio rapido:** doppio clic su `avvia.bat` (installa tutto la prima volta, poi apre il browser).
- **EXE:** doppio clic su `build_exe.bat` (da eseguire su Windows) crea `dist\SirJoeSyncroSong\SirJoeSyncroSong.exe`.
  Copia `ffmpeg.exe` nella stessa cartella. La cartella è grande (diversi GB, per PyTorch); il modello Whisper
  si scarica al primo utilizzo.
- **Senza Windows a portata di mano:** carica il progetto su GitHub ed esegui il workflow "Build EXE Windows"
  (scheda Actions): scarichi l'EXE già pronto.

## Video e link
- Puoi caricare anche **video** (MP4, MKV, MOV, WEBM, AVI…): l'audio viene estratto con ffmpeg.
- Puoi incollare il **link di un video** (YouTube e molti altri siti): l'app scarica l'audio con yt-dlp, ti permette di salvarlo (WAV)
  e, se il video ha sottotitoli, li scarica e li usa come testo (controllali: quelli automatici possono contenere errori).
  Poi la sincronizzazione parte dall'audio scaricato, senza ricaricare nulla.
- **YouTube** richiede Deno (`winget install DenoLand.Deno`) e un yt-dlp aggiornato: `avvia.bat` lo aggiorna a ogni avvio.
- Limite predefinito: 20 minuti per video (`MAX_VIDEO_MIN`), 500 MB per file caricato (`MAX_MB`).
- L'app analizza l'**audio** del video, non le immagini: eventuali testi scritti a schermo non vengono letti.
- Usa solo contenuti tuoi o di cui hai i diritti: scaricare da alcuni siti può violare i loro termini d'uso e il diritto d'autore.

## Base karaoke senza voce
Dopo la sincronizzazione l'anteprima riproduce la **base senza voce** (separata con Demucs) e le parole restano evidenziate
a tempo, perché seguono i tempi calcolati e non la voce. Il cursore "Voce originale" permette di riascoltare la voce guida
a volume regolabile (0% = karaoke puro). Se hai sincronizzato senza l'opzione Demucs, il pulsante "Rimuovi la voce originale"
crea la base in un secondo momento. La base si può scaricare in WAV.

## Evidenziazione e precisione
- Ogni parola si colora di **rosso** da sinistra a destra per tutta la sua durata, come nel karaoke classico (la pallina saltellante è stata tolta).
- **Accuratezza:** "Alta" usa il modello `medium` (molto più preciso, ma lento e con più memoria).
- **Voce isolata:** con Demucs attivo, gli attacchi delle parole vengono corretti con l'energia della sola voce, così nessuna parola parte nel silenzio.
- **Ritocchi:** tocca una riga nel karaoke e spostala di 0.1 s per volta (solo quella, da lì in poi, o tutto). Il file LRC scaricato include le correzioni.

## Conto alla rovescia nelle pause
Quando c'è una pausa strumentale di almeno 4 secondi (anche all'inizio, prima della prima parola), sopra il testo appare una
barra colorata. Negli ultimi 5 secondi si accorcia con il conto alla rovescia (5, 4, 3, 2, 1) e arriva a zero proprio
quando inizia la prima parola; durante la pausa il testo scorre già sul verso successivo.

## Grafica e pause
- L'interfaccia usa il logo "SirJoe's Karaoke Syncro Songs" (`static/logo.jpg`) con sfondo blu notte e dettagli oro e argento.
- Il conto alla rovescia compare in **ogni** pausa strumentale di almeno 4 secondi (anche a metà brano, tra due strofe o dentro un verso).
  Parte solo quando l'ultima parola della frase precedente è tutta colorata di rosso, e arriva a zero proprio quando inizia la parola successiva.

## Novità: ricerca, salvataggio, schermo intero, cancellazione
- **Carica testo:** pulsante per importare il testo da file (.txt, .lrc, .srt, .vtt, .ass); tempi e tag vengono tolti da soli.
- **Ricerca:** video/audio su YouTube, SoundCloud o dall'indirizzo di un canale/playlist/pagina (yt-dlp), e testi dal database
  aperto LRCLIB. Con "Aggiungi automaticamente il primo risultato" il primo video e il primo testo entrano subito nella
  sincronizzazione; gli altri si aggiungono con "➕". Ogni risultato si può anche salvare (MP3/MP4 per i video, .txt/.lrc per i testi).
  Pulsanti per cercare anche su Google, Genius e YouTube nel browser.
- **Salva:** MP3, WAV, FLAC (base karaoke), MP4 e AVI (video 1280×720 con testo che si colora, conto alla rovescia e logo),
  LRC (parola per parola o per righe), ASS (karaoke con colori), SRT, VTT, TXT, JSON. I formati KAR e CDG non sono supportati.
- **Schermo intero:** pulsante ⛶ (o tasto F, o doppio clic sul testo). In alto c'è il lettore con play/pausa, stop, ricomincia,
  cursore e volume: non copre il testo, scompare quando parte la musica e riappare muovendo il mouse. Barra spaziatrice = play/pausa.
- **Cancella:** scegli cosa togliere (brano/video, testo, titolo e artista, risultati di ricerca, sincronizzazione) oppure cancella tutto.

## Novità

- **Importa file già pronti**: audio, video, karaoke (LRC, SRT, VTT, ASS, KAR/MIDI, JSON, ZIP; CDG solo audio) – anche più file insieme, abbinati dal nome.
- **Libreria e playlist**: salva i brani, crea/rinomina/elimina playlist, riordina, riproduzione automatica in sequenza (avanti/indietro).
- **Schermo intero** con uscita (✕, Esc, doppio clic) e barra comandi che si nasconde quando parte la musica.
- **Uso offline**: sezione "Uso offline" → "Prepara" scarica una volta i modelli (Whisper e Demucs); poi funziona senza internet (la ricerca e i download richiedono internet).
- **Qualità audio** senza voce: standard / alta / massima (Demucs htdemucs_ft, FLAC/WAV 24 bit, MP3 320 kbps).
- **Video salvati**: caratteri grandi, righe successive visibili, scritta "Syncro By SirJoe" e logo, sfondo = videoclip del brano (se scaricato) oppure sfondo animato casuale; conto alla rovescia solo per pause strumentali > 5 secondi, dopo l'ultima parola evidenziata.

## Allineamento manuale (versione 3.6)

Sotto il karaoke, il riquadro **Allinea le parole a mano** mostra la forma d'onda e le parole su una timeline:
- trascina una parola per spostarla (spinge le vicine se si scontrano), tira i bordi per cambiare inizio e fine;
- clicca sullo sfondo per portare il brano in quel punto; rotellina o barra per scorrere; 5/10/20/40 secondi visibili;
- **Inizio = qui**, **Fine = qui**, **Riga inizia qui**, ±0,05 s, frecce ← → (Maiusc 0,1 s, Alt per la fine), Annulla/Ripeti (Ctrl+Z / Ctrl+Y);
- **Marca a tempo**: avvia il brano e premi la barra spaziatrice all'inizio di ogni parola.
Le modifiche valgono per i video e i file che salvi dopo; con «Salva le modifiche in libreria» restano nel brano.

## Novità versione 3.7
- **Video karaoke già pronti**: importa con "Importa file" video (mp4, avi, mkv, webm, mov…) anche senza testo: vengono riprodotti nel player (convertiti in MP4 se il browser non li legge). Coppie CDG + mp3 con lo stesso nome vengono trasformate in video.
- **Sfondo personale**: nella sezione Salva scegli "📁 Scegli il mio video o la mia immagine di sfondo…"; viene salvato con il brano in libreria.
- **Accordi e note** sopra il testo (menu "Accordi / note"): accordi stimati dalla base (triadi, ±0,2 s); le note cantate sono affidabili solo se la voce è stata separata. Nomi in italiano (Do, Re…) o inglese (C, D…).
- **Controllo libreria e playlist** (🔧): esegue una prova completa e mostra versione, cartella e eventuali errori. Se la porta 8000 è occupata, il programma ne usa un'altra e lo segnala.
- Se i pulsanti non rispondono: aggiornare TUTTA la cartella, chiudere le vecchie finestre nere, riavviare e verificare "Versione 3.7" in basso.

## Versioni installabili (Windows e Mac)
Dalla pagina **Releases** del repository scarichi:
- `SirJoeKaraoke-Windows.zip` → estrai, doppio clic su `SirJoeKaraoke.exe` (se Windows avvisa: "Ulteriori informazioni" → "Esegui comunque").
- `SirJoeKaraoke-Mac-AppleSilicon.zip` (Mac M1/M2/M3/M4) oppure `SirJoeKaraoke-Mac-Intel.zip` → estrai, doppio clic su `Avvia-SirJoe.command` (la prima volta: clic destro → Apri).
ffmpeg è già incluso. La libreria si salva nella cartella `SirJoe Karaoke Libreria` dentro la tua cartella utente. I modelli di riconoscimento vocale si scaricano al primo uso (serve internet). Per scaricare da YouTube serve anche Deno.
Le versioni si creano da sole con GitHub Actions (`.github/workflows/build.yml`) quando si pubblica un tag, ad esempio `v3.7`.
