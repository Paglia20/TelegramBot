# Monitor annunci eBay

Controlla 16 mercati eBay (Italia, Germania, Svizzera, Francia, Belgio, Olanda, Austria, Spagna, Polonia, Irlanda, Regno Unito, USA, Canada, Australia, Hong Kong e Singapore) tramite la Browse API ufficiale e manda su Telegram i nuovi annunci che contengono le tue parole. Parole, esclusioni, timer e mercati si gestiscono dal bot.

## Dashboard

Mentre il bot gira, apri http://127.0.0.1:8765 nel browser del computer dove gira. Vedi se il bot è attivo, quante chiamate eBay ti restano, lo stato di ogni mercato, le parole, gli ultimi annunci trovati e gli eventi recenti. Da lì puoi anche mettere in pausa, aggiungere o togliere parole ed esclusioni, attivare mercati e cambiare il timer. La pagina è raggiungibile solo dal tuo computer. Se la porta 8765 è occupata, cambiala con `DASHBOARD_PORT` nel file `.env`.

### Dashboard da link Telegram

Metti nel file `.env` l'indirizzo pubblico del server (su Railway è il dominio che ti assegna, su un Raspberry quello del tunnel Cloudflare). Da quel momento il comando /dashboard, o il bottone "Dashboard" in /menu, ti manda un link personale: vale 10 minuti, si usa una volta sola e il browser con cui lo apri resta collegato per 30 giorni. Chi apre l'indirizzo senza link vede solo "accesso richiesto". Se perdi il telefono o pensi che un link sia finito nelle mani sbagliate, /revoca_dashboard scollega tutti i browser. Usa sempre un indirizzo https.

## Comandi

/menu apre il pannello con i bottoni. Poi ci sono le scorciatoie: /aggiungi, /rimuovi, /escludi, /includi, /timer, /mercati, /stato, /dashboard, /pausa, /riprendi. Puoi aggiungere più parole insieme separandole con una virgola.

## Il limite di eBay

eBay concede 5000 ricerche al giorno. Ogni ciclo fa una ricerca per mercato (le parole vengono raggruppate in una sola ricerca, fino a 5 per volta). Con tutti i 16 mercati attivi il ciclo più veloce sostenibile è circa ogni 5 minuti; con 7 mercati circa ogni 2 minuti e un quarto. I mercati aggiunti in futuro partono accesi, quelli che hai spento restano spenti. Se imposti un timer più corto il programma usa comunque il minimo sostenibile e te lo dice in /timer. Per andare più veloce: disattiva mercati che non ti servono, oppure chiedi più quota a eBay con l'Application Growth Check (gratuito).

## Impostazioni avanzate

Tutto in cima a `config.py`: cosa fare quando un annuncio cambia prezzo (`ON_LISTING_CHANGE`), la deduplicazione tra mercati, quante foto mandare, se cercare solo oggetti che si trovano nel paese del mercato (`EBAY_ONLY_LOCAL_ITEMS`).

Parole, esclusioni, timer e storico stanno in `monitor.db`. Il programma non ti manda mai annunci vecchi: per ogni parola notifica solo quelli pubblicati dopo che l'hai aggiunta, con un margine di 15 minuti.
