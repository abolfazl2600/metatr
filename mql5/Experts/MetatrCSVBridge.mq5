//+------------------------------------------------------------------+
//| MetatrCSVBridge.mq5                                               |
//| Read-only bridge: completed OHLC candles -> FILE_COMMON CSV.     |
//| NO trading functions, NO WebRequest, NO account credentials.     |
//+------------------------------------------------------------------+
#property strict
#property version "1.00"
#property description "Read-only closed-bar exporter for Python on macOS/Wine"

input int InpBarsToExport = 300;   // Keep at least 52 completed candles
input int InpRefreshSeconds = 5;  // Poll for a newly closed candle

string   g_target_file = "";
string   g_staging_file = "";
string   g_timeframe = "";
datetime g_last_exported = 0;

string SafeFilenamePart(const string value)
  {
   string result = "";
   for(int i = 0; i < StringLen(value); i++)
     {
      ushort c = StringGetCharacter(value, i);
      if((c >= 48 && c <= 57) || (c >= 65 && c <= 90) ||
         (c >= 97 && c <= 122) || c == 95 || c == 45)
         result += StringSubstr(value, i, 1);
      else
         result += "_";
     }
   return result;
  }

int OnInit()
  {
   if(InpBarsToExport < 52 || InpBarsToExport > 100000 || InpRefreshSeconds < 1)
      return INIT_PARAMETERS_INCORRECT;

   g_timeframe = EnumToString(_Period);
   StringReplace(g_timeframe, "PERIOD_", "");
   g_target_file = "metatr_" + SafeFilenamePart(_Symbol) + "_" + g_timeframe + ".csv";
   g_staging_file = g_target_file + ".tmp";

   if(!EventSetTimer(InpRefreshSeconds))
     {
      PrintFormat("Bridge: timer failed; error=%d", GetLastError());
      return INIT_FAILED;
     }
   Print("Bridge output: ", TerminalInfoString(TERMINAL_COMMONDATA_PATH),
         "\\Files\\", g_target_file);
   ExportSnapshot();
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTimer()
  {
   ExportSnapshot();
  }

void ExportSnapshot()
  {
   // Bar zero is still forming. Bar one is the most recent CLOSED bar.
   datetime latest_closed = iTime(_Symbol, _Period, 1);
   if(latest_closed <= 0 || latest_closed == g_last_exported)
      return;

   MqlRates rates[];
   int count = CopyRates(_Symbol, _Period, 1, InpBarsToExport, rates);
   if(count < 52)
      return;
   // The physical array is oldest -> newest according to CopyRates docs.
   if(rates[count - 1].time != latest_closed)
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   ResetLastError();
   int handle = FileOpen(g_staging_file,
                         FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON,
                         ',', CP_UTF8);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("Bridge: FileOpen failed: %d", GetLastError());
      return;
     }

   bool ok = (FileWrite(handle, "timestamp", "open", "high", "low", "close",
                        "volume", "symbol", "timeframe", "schema_version") > 0);
   for(int i = 0; i < count && ok; i++)
     {
      uint written = FileWrite(handle,
                               IntegerToString((long)rates[i].time),
                               DoubleToString(rates[i].open, digits),
                               DoubleToString(rates[i].high, digits),
                               DoubleToString(rates[i].low, digits),
                               DoubleToString(rates[i].close, digits),
                               IntegerToString((long)rates[i].tick_volume),
                               _Symbol, g_timeframe, 1);
      if(written == 0)
         ok = false;
     }
   FileFlush(handle);
   FileClose(handle);
   if(!ok)
     {
      Print("Bridge: CSV write incomplete; previous snapshot preserved");
      FileDelete(g_staging_file, FILE_COMMON);
      return;
     }

   // Publish the completed snapshot only after its handle is closed.
   ResetLastError();
   if(!FileMove(g_staging_file, FILE_COMMON, g_target_file,
                FILE_COMMON | FILE_REWRITE))
     {
      PrintFormat("Bridge: failed to publish snapshot: %d", GetLastError());
      return;
     }
   g_last_exported = latest_closed;
   PrintFormat("Bridge: published %d closed candles: %s (%s)",
               count, g_target_file, TimeToString(latest_closed));
  }
