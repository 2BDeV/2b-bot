# Google-belépéses 2B Bot konzol

## Indítás

A Procfile most a `python console_launcher.py` parancsot használja. Helyi indításnál is ezt használd. A launcher importálja a változatlan dc_bot.py modult, hozzáadja a konzolt, Waitress-szel elindítja a Flask-alkalmazást, majd a Discord-botot. A meglévő sakk és webhelyfigyelés változatlanul megmarad. Egyetlen folyamat/példány használata szükséges: a naplópuffer, parancszár és sakkállapot memóriában található.

## Google Cloud

1. Hozz létre vagy válassz Google Cloud-projektet, és állítsd be a Google Auth Platform hozzájárulási képernyőjét.
2. Tesztelési állapotban add hozzá tesztfelhasználóként: benedek.balint.kiss@gmail.com.
3. Hozz létre webalkalmazás típusú OAuth-klienst.
4. Engedélyezett átirányítási URI: https://2bbot-heroku.2bdevon.top/console/callback. Eltérő domainnél a tényleges HTTPS BASE_URL utáni /console/callback útvonalat add meg, pontos egyezéssel.
5. A szolgáltatód titkos környezeti változói között állítsd be a GOOGLE_CLIENT_ID és GOOGLE_CLIENT_SECRET értékét. Ne commitold őket.
6. Állíts be legalább 32 karakteres, kriptográfiailag véletlen CONSOLE_SESSION_SECRET értéket. Minden újraindításnál ugyanazt a tárolt titkot használd; cseréje érvényteleníti a korábbi munkameneteket.
7. BASE_URL legyen az alkalmazás külső HTTPS-címe, útvonal nélkül. Az alapérték https://2bbot-heroku.2bdevon.top.
8. CONSOLE_ALLOWED_EMAILS opcionális, vesszővel elválasztott engedélyezési lista. Alapértéke kizárólag benedek.balint.kiss@gmail.com. A program ellenőrzi a Google által hitelesített e-mail-címet és az email_verified mezőt.

Hiányzó OAuth-beállítások esetén a konzol 503 hibával letiltva marad; a bot ettől még elindul. A konzolhoz Authlib kezeli az OpenID Connect állapot-, nonce- és ID-token-ellenőrzést, PKCE használatával. HTTPS és biztonságos munkamenetsüti szükséges; sima HTTP helyi használatra nincs engedélyezve.

## Használat

A /console Discord-parancs csak a kérő számára látható hivatkozást ad. A link ismerete nem jogosít hozzáférésre. Bejelentkezés után a /console oldalon követhető a telepítés indítása óta tárolt stdout/stderr és a discord napló. Nem szolgáltatói történeti napló: legfeljebb 2000 memóriabeli bejegyzés tárolódik, újraindításkor elveszik.

Tetszőleges nem interaktív /bin/sh rendszerparancs futtatható, egyszerre egy, legfeljebb 60 másodpercig. A folyamatcsoportot befejezéskor megszüntetjük. Nincs PTY, interaktív stdin vagy parancsok közötti shell-állapot; a cd csak ugyanazon parancson belül érvényes. Interaktív szerkesztő, sudo-jelszóbekérés vagy tartós háttérfolyamat indítása nem támogatott. Ez nem emeli meg a bot operációsrendszer-jogosultságait.

Külön botvezérlés: :reload újraszinkronizál; :reset törli a memóriabeli és a globális Discord-parancsokat az újraindításig; :restart újraindítja a Python-folyamatot; :stop lezárja a botot és az alkalmazást. A szolgáltató a leállt folyamatot automatikusan újraindíthatja.

## Biztonsági és telepítési korlátok

Ez szándékosan teljes távoli parancsvégrehajtási jogosultság: a jóváhagyott fiók a bot fájljaihoz, tokenjeihez és környezeti változóihoz is hozzáférhet, és a saját konzolját is módosíthatja. Ne oszd meg a Google-fiókot, használj többtényezős hitelesítést, és ne adj hozzá nem megbízható címet. A napló/parancssor titkot is tartalmazhat; nincs automatikus titokkitakarás. A kijelentkezés a böngésző sütijét törli, az ellopott munkamenet globális visszavonásához cseréld a CONSOLE_SESSION_SECRET értékét. A munkamenet legfeljebb 30 percig használható a belépéstől.

Heroku-jellegű környezetben a fájlrendszer változásai nem tartósak; a konzolból telepített csomagok vagy szerkesztések új telepítéskor eltűnhetnek. A tartós kódváltozásokat GitHub-commitként, a csomagokat requirements.txt-ben kezeld.

A tárolóban meglévő .env fájlt ez a módosítás nem olvassa vagy módosítja. Ha valódi titkok kerültek bele, vond vissza/cseréld azokat, és külön távolítsd el a fájlt a követett fájlok és szükség szerint a Git-előzmények közül.

## Ellenőrzési lista telepítés után

- Függőségek települnek és a launcher elindul.
- OAuth-konfiguráció nélkül /console 503 hibát ad, de a Discord-bot működik.
- A megadott Google-fiók beléphet; másik fiók 403 hibát kap.
- Belépés nélkül a logs/execute útvonalak 401 hibát adnak.
- CSRF-token nélküli vagy más eredetű POST 403 hibát kap.
- Egy rövid rendszerparancs kimenete megjelenik; egy párhuzamos parancs 409 hibát kap.
- A 60 másodperces korlát megszünteti a túl hosszú parancsot.
- :reload, /ping és a sakkoldal továbbra is működik.

A Google OAuth, Discord és hosztolt környezet teljes integrációs tesztje csak a tényleges titkok és telepítés beállítása után végezhető el.
