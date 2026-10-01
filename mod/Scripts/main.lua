-- DuneLektorPL: polski lektor sterowany z gry.
-- Przechwytuje tekst pokazywany w widgetach dialogu/napisów, dopasowuje go do tabeli lines.lua
-- i przez plik komend każe playerowi odtworzyć nagranie kwestii.
--
-- Wydanie: obok moda leżą Player\DuneLektorPlayer.exe i Audio\ — wszystkie ścieżki względem folderu moda,
-- log w <mod>\lektor.log. Bez exe (komputer autora) tryb deweloperski: F:\DuneLektor\player\player.py,
-- log w F:\DuneLektor\build\spy.log.

local MOD_ROOT = debug.getinfo(1, "S").source:gsub("^@", ""):gsub("/", "\\"):match("^(.*)\\Scripts\\[^\\]+%.lua$") or "."
local INI_PATH = MOD_ROOT .. "\\mod.ini"
local PLAYER_EXE = MOD_ROOT .. "\\Player\\DuneLektorPlayer.exe"
local RELEASE = false
do local f = io.open(PLAYER_EXE, "rb"); if f then f:close(); RELEASE = true end end

local DEV = "F:\\DuneLektor\\"
local LOG_PATH = RELEASE and (MOD_ROOT .. "\\lektor.log") or (DEV .. "build\\spy.log")
local CMD_PATH = RELEASE and (MOD_ROOT .. "\\Player\\cmd.txt") or (DEV .. "player\\cmd.txt")

local function log(msg)
    local f = io.open(LOG_PATH, "a")
    if f then f:write(os.date("%H:%M:%S"), " ", msg, "\n"); f:close() end
end

local function cmd(line)
    local f = io.open(CMD_PATH, "a")
    if f then f:write(line, "\n"); f:close() end
end

-- Tabela tekst -> id wiersza (wygenerowana).
local okLines, LINES = pcall(require, "lines")
if not okLines then LINES = {}; log("BLAD: nie wczytano lines.lua: " .. tostring(LINES)) end

-- Normalizacja identyczna z tools/make_lines_lua.py: bez znaczników, małe litery, tylko litery/cyfry.
local function norm(s)
    -- ekran tworzenia postaci: '<Speaker>MATKA WIELEBNA: </><NpcText>Podejdź.</>' -> wywal całą nazwę mówcy
    s = s:gsub("<Speaker>.-</>", "")
    s = s:gsub("<[^>]+>", "")
    s = s:lower()
    -- polskie wielkie litery (UTF-8) -> małe; string.lower ich nie zna
    local map = { ["Ą"]="ą",["Ć"]="ć",["Ę"]="ę",["Ł"]="ł",["Ń"]="ń",["Ó"]="ó",["Ś"]="ś",["Ź"]="ź",["Ż"]="ż" }
    for k, v in pairs(map) do s = s:gsub(k, v) end
    -- ASCII: tylko litery/cyfry; UTF-8: tylko polskie małe litery, reszta (cudzysłowy, pauzy, wielokropek) -> spacja
    s = s:gsub("[^%w\128-\255]+", " ")
    local pl = { ["ą"]=1,["ć"]=1,["ę"]=1,["ł"]=1,["ń"]=1,["ó"]=1,["ś"]=1,["ź"]=1,["ż"]=1 }
    s = s:gsub("[\194-\244][\128-\191]*", function(ch) return pl[ch] and ch or " " end)
    s = s:gsub("%s+", " ")
    s = s:gsub("^%s+", ""):gsub("%s+$", "")
    return s
end

local lastRow, lastTime = nil, 0
local function speak(text, source)
    local key = norm(text)
    if key == "" then return end
    local row = LINES[key]
    if not row then
        log("NOMATCH [" .. source .. "] " .. text:sub(1, 160))
        return
    end
    local now = os.clock()
    if row == lastRow and now - lastTime < 2.0 then return end
    lastRow, lastTime = row, now
    cmd("PLAY " .. row)
    log("PLAY " .. row .. " [" .. source .. "] " .. text:sub(1, 120))
end

-- 1. Napisy (przerywniki, rozmowy z napisami): Blueprint ustawia W_SubtitleEntry.BodyText przez RichTextBlock:SetText.
-- Tylko ten widget — hook na wszystkie TextBlock/RichTextBlock (z czasu rozpoznania) wywracał grę w menu Mod Settings
-- (30.09: EXCEPTION_ACCESS_VIOLATION w UE4SS przy odczycie tekstów świeżo budowanych widgetów).
local okHook = pcall(function()
    RegisterHook("/Script/UMG.RichTextBlock:SetText", function(ctx, inText)
        pcall(function()
            local o = ctx:get()
            if not o or not o:IsValid() or o:GetFName():ToString() ~= "BodyText" then return end
            local text = inText:get():ToString()
            if type(text) == "string" and #text >= 2 then speak(text, "BodyText") end
        end)
    end)
end)
log(okHook and "HOOKED RichTextBlock:SetText (BodyText)" or "BLAD: hook RichTextBlock:SetText")


-- 2. Okno dialogu (przesłuchanie, rozmowy z NPC): jego tekst ustawia kod C++ bezpośrednio, więc żaden hook
--    UFunction tego nie widzi. Zamiast tego co 200 ms odczytujemy pole m_SpeakerText i reagujemy na zmianę.
local lastDialogText = ""
LoopAsync(200, function()
    pcall(function()
        local win = FindFirstOf("W_DialogueWindow_C")
        if not win or not win:IsValid() then lastDialogText = ""; return end
        local tb = win:GetPropertyValue("m_SpeakerText")
        if not tb or not tb:IsValid() then return end
        local text = tb:GetText():ToString()
        if text == lastDialogText then return end
        lastDialogText = text
        if #text < 2 then return end
        local who = ""
        pcall(function() who = win:GetPropertyValue("m_SpeakerName"):GetText():ToString() end)
        log("DIALOG [" .. who .. "] " .. text:sub(1, 160))
        speak(text, "DIALOG")
    end)
    return false
end)

-- Automatyczny start playera (bez okna). Druga kopia sama się zamknie (mutex w player.py),
-- a player kończy pracę razem z grą — nie trzeba go uruchamiać ręcznie.
-- Ustawienia (mod.ini obok mod.json): player czyta je sam przy starcie (ścieżka w argumencie),
-- a zmiany z menu Mod Settings przekazujemy mu na żywo komendami.
local function q(p) return '"' .. p .. '"' end
if RELEASE then
    os.execute('start "" ' .. q(PLAYER_EXE) .. ' ' .. q(INI_PATH) .. ' --audio ' .. q(MOD_ROOT .. "\\Audio")
        .. ' --cmd ' .. q(CMD_PATH) .. ' --log ' .. q(MOD_ROOT .. "\\Player\\player.log"))
else
    local PYTHONW = (os.getenv("LOCALAPPDATA") or "") .. "\\Programs\\Python\\Python312\\pythonw.exe"
    os.execute('start "" ' .. q(PYTHONW) .. ' ' .. q(DEV .. "player\\player.py") .. ' ' .. q(INI_PATH))
end
log("player uruchomiony (" .. (RELEASE and "wydanie" or "dev") .. "), ustawienia: " .. INI_PATH)

-- Zakładka „Mods” w ustawieniach gry (WPS Framework + Mod Settings) — opcjonalnie: bez nich lektor działa dalej.
-- Aplikacja WPS i rejestracja MUSZĄ żyć przez całą grę (globalna tabela): wcześniej były tylko lokalne w tej funkcji,
-- Lua je sprzątał (GC), Mod Settings uznawał, że mod zniknął, i strona Mods w grze nagle robiła się pusta (30.09).
DuneLektorPL_Keep = DuneLektorPL_Keep or {}
pcall(function()
    local okF, F = pcall(require, "../../WPS_Framework/Scripts/api")
    local okS, Settings = pcall(require, "../../ModSettings/Scripts/api")
    if not (okF and okS) then log("Mod Settings niedostępne — suwaki tylko w mod.ini"); return end
    local app = F.new{ id = "DuneLektorPL", version = "0.3.1" }
    DuneLektorPL_Keep.app = app
    local ok, why = pcall(Settings.register, app, debug.getinfo(1, "S").source, {
        onSave = function(values)
            local v = values.DuneLektorPL or {}
            if v.Glosnosc ~= nil then cmd(string.format("GLOSNOSC %d", v.Glosnosc)) end   -- skala w playerze
            if v.Tempo ~= nil then cmd(string.format("SPEED %.2f", v.Tempo)) end
            if v.Pomijanie ~= nil then cmd(string.format("GRACE %.1f", v.Pomijanie)) end
            log(string.format("USTAWIENIA: glosnosc %s, tempo %s, pomijanie %s",
                tostring(v.Glosnosc), tostring(v.Tempo), tostring(v.Pomijanie)))
        end,
    })
    if ok then DuneLektorPL_Keep.settings = why end   -- przy sukcesie pcall zwraca obiekt rejestracji
    log(ok and "Mod Settings: zarejestrowano suwaki" or ("Mod Settings: rejestracja nieudana: " .. tostring(why)))
end)

log("=== DuneLektorPL start, wpisów w tabeli: " .. tostring((function() local n = 0; for _ in pairs(LINES) do n = n + 1 end; return n end)()) .. " ===")
