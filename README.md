# Monitor annunci eBay

Controlla eBay Italia, Germania, Svizzera, Francia, Belgio e Olanda tramite la Browse API ufficiale e manda su Telegram i nuovi annunci che contengono le tue parole. Parole, esclusioni, timer e mercati si gestiscono dal bot.

## Cosa ti serve

1. Python 3.10 o più recente.
2. Un bot Telegram: scrivi a @BotFather, comando /newbot, copia il token.
3. Le chiavi eBay: su developer.ebay.com vai in Application Keys e crea il keyset **Production**. Ti servono App ID (Client ID) e Cert ID (Client Secret). Per attivare il keyset eBay chiede di gestire le notifiche di cancellazione account: scegli l'esenzione, questo programma non conserva dati personali degli utenti eBay (dell'annuncio salva ID, titolo, prezzo e link; del venditore solo un hash anonimo usato contro i doppioni).

## Installazione

```bash
cd TelegramBot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Apri `.env` e inserisci `TELEGRAM_TOKEN`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`. Lascia vuoto `CHAT_ID` per ora.

`EBAY_ENV` sceglie l'ambiente eBay: `sandbox` per le prove con le chiavi Sandbox (contengono `SBX`), `production` per l'uso vero con le chiavi Production (contengono `PRD`). In sandbox gli annunci sono pochi e finti, quasi tutti su eBay USA, e il programma ti notifica anche quelli vecchi, al massimo 5 per ciclo, cosi' puoi vedere subito come arrivano i messaggi. Sandbox e produzione usano due database separati (`monitor-sandbox.db` e `monitor.db`), quindi parole e storico di prova non finiscono in quelli veri.

## Primo avvio

```bash
python main.py
```

Scrivi qualsiasi cosa al bot: ti risponde con il tuo CHAT_ID. Mettilo nel `.env`, ferma il programma con Ctrl+C e riavvialo. Da qui in poi scrivi /menu.

Prima di lasciarlo girare, lancia una volta `python check_ebay.py`. Verifica le chiavi, prova tutti i mercati e controlla se la ricerca combinata di più parole funziona. Se ti dice che una parola sparisce nella ricerca combinata, metti `EBAY_QUERY_MODE = "single"` in `config.py`.

## Dashboard

Mentre il bot gira, apri http://127.0.0.1:8765 nel browser del computer dove gira. Vedi se il bot è attivo, quante chiamate eBay ti restano, lo stato di ogni mercato, le parole, gli ultimi annunci trovati e gli eventi recenti. Da lì puoi anche mettere in pausa, aggiungere o togliere parole ed esclusioni, attivare mercati e cambiare il timer. La pagina è raggiungibile solo dal tuo computer. Se la porta 8765 è occupata, cambiala con `DASHBOARD_PORT` nel file `.env`.

### Dashboard da link Telegram (quando il bot gira su un server)

Metti nel file `.env` l'indirizzo pubblico del server, per esempio `DASHBOARD_PUBLIC_URL=https://monitor.tuodominio.it` (su Railway è il dominio che ti assegna, su un Raspberry quello del tunnel Cloudflare). Da quel momento il comando /dashboard, o il bottone "Dashboard" in /menu, ti manda un link personale: vale 10 minuti, si usa una volta sola e il browser con cui lo apri resta collegato per 30 giorni. Chi apre l'indirizzo senza link vede solo "accesso richiesto". Se perdi il telefono o pensi che un link sia finito nelle mani sbagliate, /revoca_dashboard scollega tutti i browser. Usa sempre un indirizzo https.

## Comandi

/menu apre il pannello con i bottoni. Poi ci sono le scorciatoie: /aggiungi, /rimuovi, /escludi, /includi, /timer, /mercati, /stato, /dashboard, /pausa, /riprendi. Puoi aggiungere più parole insieme separandole con una virgola.

## Test

Dalla cartella del progetto, con il `.venv` attivo:

```bash
python -m unittest -v
```

I test usano database temporanei e un eBay finto: non toccano `monitor.db`, non fanno chiamate vere e non consumano quota. Se un giorno installi pytest (`pip install pytest`), basta scrivere `pytest`.

## Il limite di eBay

eBay concede 5000 ricerche al giorno. Ogni ciclo fa una ricerca per mercato (le parole vengono raggruppate in una sola ricerca, fino a 5 per volta). Con 6 mercati il ciclo più veloce sostenibile è circa ogni 2 minuti. Se imposti un timer più corto il programma usa comunque il minimo sostenibile e te lo dice in /timer. Per andare più veloce: disattiva mercati che non ti servono, oppure chiedi più quota a eBay con l'Application Growth Check (gratuito).

## Impostazioni avanzate

Tutto in cima a `config.py`: cosa fare quando un annuncio cambia prezzo (`ON_LISTING_CHANGE`), la deduplicazione tra mercati, quante foto mandare, se cercare solo oggetti che si trovano nel paese del mercato (`EBAY_ONLY_LOCAL_ITEMS`).

Parole, esclusioni, timer e storico stanno in `monitor.db`. Il programma non ti manda mai annunci vecchi: per ogni parola notifica solo quelli pubblicati dopo che l'hai aggiunta, con un margine di 15 minuti.
