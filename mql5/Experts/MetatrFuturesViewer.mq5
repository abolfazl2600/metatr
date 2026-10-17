// One-way external-data viewer. Native compile/Wine acceptance is REQUIRED.
#property strict
#property version "1.01"
#property description "EXTERNAL / SIMULATED DATA viewer; execution DISABLED"

input string CommonSubfolder="metatr\\REPLACE_WITH_PRINTED_MARKET_HASH";
input string ExpectedProduct="BIP-20DEC30-CDE";
input string ExpectedStrategy="momentum";
input string ExpectedVersion="1.0.0";
input int PollSeconds=5;
input int MaximumBars=1000;
input int MaximumAgeBars=3;

string last_run="", custom_symbol="", error_reason="";
long display_chart=0;
const string PREFIX="metatr_external_";
const string MANIFEST_HEADER="schema_version,market,source,capture_utc,latest_bar_utc,bar_count,signal_count,bars_file,bars_bytes,bars_sha256,signals_file,signals_bytes,signals_sha256,run_id,data_sha256,contract_sha256,strategy_id,strategy_version,config_sha256,status,execution,label";
const string BARS_HEADER="timestamp,open,high,low,close,volume,product_id,timeframe";
const string SIGNALS_HEADER="bar_open_utc,decision_utc,action,strategy_id,strategy_version,config_sha256,reason,data_sha256";

bool Fail(const string reason) { error_reason=reason; return false; }

long FindDisplayChart()
{
   // Chart IDs are process-local: rediscover a retained chart after EA/terminal restart.
   for(long chart=ChartFirst();chart>=0;chart=ChartNext(chart))
      if(ChartSymbol(chart)==custom_symbol && ChartPeriod(chart)==PERIOD_M15)
         return chart;
   return 0;
}

bool DigitsOnly(const string s)
{
   if(StringLen(s)<1 || StringLen(s)>14) return false;
   for(int i=0;i<StringLen(s);i++)
   { ushort c=StringGetCharacter(s,i); if(c<'0' || c>'9') return false; }
   return true;
}

bool HexDigest(const string s)
{
   if(StringLen(s)!=64) return false;
   for(int i=0;i<64;i++)
   { ushort c=StringGetCharacter(s,i); if(!((c>='0' && c<='9') || (c>='a' && c<='f'))) return false; }
   return true;
}

bool Numeric(const string s,double &value)
{
   int n=StringLen(s),i=0,digits=0;
   if(n<1 || n>50) return false;
   while(i<n && StringGetCharacter(s,i)>='0' && StringGetCharacter(s,i)<='9') { i++; digits++; }
   if(digits==0) return false;
   if(i<n && StringGetCharacter(s,i)=='.')
   {
      i++; int fraction=0;
      while(i<n && StringGetCharacter(s,i)>='0' && StringGetCharacter(s,i)<='9') { i++; fraction++; }
      if(fraction==0) return false;
   }
   if(i<n && (StringGetCharacter(s,i)=='e' || StringGetCharacter(s,i)=='E'))
   {
      i++; if(i<n && (StringGetCharacter(s,i)=='+' || StringGetCharacter(s,i)=='-')) i++;
      int exponent=0;
      while(i<n && StringGetCharacter(s,i)>='0' && StringGetCharacter(s,i)<='9') { i++; exponent++; }
      if(exponent==0) return false;
   }
   if(i!=n) return false;
   value=StringToDouble(s);
   return MathIsValidNumber(value) && value>=0;
}

bool LoadBytes(const string name,const long expected_size,const string checksum,string &text)
{
   int h=FileOpen(CommonSubfolder+"\\"+name,FILE_READ|FILE_BIN|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(h==INVALID_HANDLE) return Fail("missing file "+name);
   ulong size=FileSize(h);
   if(size<1 || size>8000000 || (expected_size>=0 && size!=(ulong)expected_size))
   { FileClose(h); return Fail("file byte limit/size mismatch"); }
   uchar data[],key[],hash[];
   ArrayResize(data,(int)size);
   uint got=FileReadArray(h,data,0,(int)size); FileClose(h);
   if(got!=(uint)size) return Fail("partial file read");
   for(int i=0;i<ArraySize(data);i++) if(data[i]>127 || data[i]==0) return Fail("non ASCII payload");
   if(checksum!="")
   {
      if(!HexDigest(checksum) || CryptEncode(CRYPT_HASH_SHA256,data,key,hash)!=32)
         return Fail("SHA256 failed");
      string hex=""; for(int i=0;i<ArraySize(hash);i++) hex+=StringFormat("%02x",(uint)hash[i]);
      if(hex!=checksum) return Fail("payload SHA256 mismatch");
   }
   text=CharArrayToString(data,0,ArraySize(data),CP_UTF8);
   return true;
}

bool Lines(const string text,const string header,string &lines[])
{
   int n=StringSplit(text,'\n',lines);
   if(n<3 || lines[0]!=header || lines[n-1]!="") return Fail("CSV header/termination mismatch");
   ArrayResize(lines,n-1);
   return true;
}

void Banner(const string state,const color tone)
{
   string text="EXTERNAL / SIMULATED DATA | "+ExpectedProduct+" M15 | "+state+
               " | execution=DISABLED | UTC, volume x1e-8 source units";
   Comment(text);
   if(display_chart>0)
   {
      string name=PREFIX+"status";
      if(ObjectFind(display_chart,name)<0) ObjectCreate(display_chart,name,OBJ_LABEL,0,0,0);
      ObjectSetInteger(display_chart,name,OBJPROP_CORNER,CORNER_LEFT_UPPER);
      ObjectSetInteger(display_chart,name,OBJPROP_XDISTANCE,12);
      ObjectSetInteger(display_chart,name,OBJPROP_YDISTANCE,16);
      ObjectSetInteger(display_chart,name,OBJPROP_COLOR,tone);
      ObjectSetString(display_chart,name,OBJPROP_TEXT,text);
      ChartRedraw(display_chart);
   }
}

bool Refresh()
{
   string text,lines[],m[];
   if(!LoadBytes("manifest.csv",-1,"",text) || !Lines(text,MANIFEST_HEADER,lines)) return false;
   if(ArraySize(lines)!=2 || StringSplit(lines[1],',',m)!=22) return Fail("manifest row count");
   string expected="coinbase:BTC:FUTURE:"+ExpectedProduct+":M15";
   if(m[0]!="1" || m[1]!=expected || m[19]!="VALID" || m[20]!="DISABLED" ||
      m[21]!="EXTERNAL_SIMULATED_DATA" || m[16]!=ExpectedStrategy || m[17]!=ExpectedVersion)
      return Fail("identity/schema/model/status mismatch");
   for(int i=13;i<=15;i++) if(!HexDigest(m[i])) return Fail("invalid pinned digest");
   if(!HexDigest(m[18])) return Fail("invalid model digest");
   if(!DigitsOnly(m[3]) || !DigitsOnly(m[4]) || !DigitsOnly(m[5]) || !DigitsOnly(m[6]) ||
      !DigitsOnly(m[8]) || !DigitsOnly(m[11])) return Fail("invalid manifest number");
   long capture=StringToInteger(m[3]),latest=StringToInteger(m[4]),now=(long)TimeGMT();
   int count=(int)StringToInteger(m[5]),signals=(int)StringToInteger(m[6]);
   if(capture>now || capture<latest+900 || latest+900>now || latest%900!=0 ||
      now-latest>(MaximumAgeBars+1)*900 || count<1 || count>MaximumBars || signals<1 || signals>count)
      return Fail("STALE/future/unbounded source");
   if(m[7]!="bars_"+m[13]+".csv" || m[10]!="signals_"+m[13]+".csv")
      return Fail("unsafe/mixed payload name");
   string bar_text,signal_text,bar_lines[],signal_lines[];
   if(!LoadBytes(m[7],StringToInteger(m[8]),m[9],bar_text) ||
      !LoadBytes(m[10],StringToInteger(m[11]),m[12],signal_text) ||
      !Lines(bar_text,BARS_HEADER,bar_lines) || !Lines(signal_text,SIGNALS_HEADER,signal_lines)) return false;
   if(ArraySize(bar_lines)!=count+1 || ArraySize(signal_lines)!=signals+1) return Fail("payload row counts");
   MqlRates rates[]; ArrayResize(rates,count); long previous=0;
   for(int i=0;i<count;i++)
   {
      string f[]; if(StringSplit(bar_lines[i+1],',',f)!=8 || !DigitsOnly(f[0]) ||
         f[6]!=ExpectedProduct || f[7]!="M15") return Fail("bad candle identity");
      long opened=StringToInteger(f[0]); double o,h,l,c,v;
      if(opened<=previous || opened%900!=0 || opened+900>now ||
         !Numeric(f[1],o) || !Numeric(f[2],h) || !Numeric(f[3],l) ||
         !Numeric(f[4],c) || !Numeric(f[5],v) || MathMin(o,MathMin(h,MathMin(l,c)))<=0 ||
         h<MathMax(o,MathMax(l,c)) || l>MathMin(o,MathMin(h,c)) || v>9e10)
         return Fail("invalid OHLCV/UTC sequence");
      ZeroMemory(rates[i]); rates[i].time=(datetime)opened;
      rates[i].open=o; rates[i].high=h; rates[i].low=l; rates[i].close=c;
      // Preserve fractional source volume with an explicitly labeled 1e8 display scale.
      rates[i].real_volume=(long)MathRound(v*100000000.0); rates[i].tick_volume=rates[i].real_volume;
      previous=opened;
   }
   if(previous!=latest) return Fail("latest candle mismatch");
   long signal_times[]; string actions[]; int bar_positions[];
   ArrayResize(signal_times,signals); ArrayResize(actions,signals); ArrayResize(bar_positions,signals);
   previous=0;
   for(int i=0;i<signals;i++)
   {
      string f[]; if(StringSplit(signal_lines[i+1],',',f)!=8 || !DigitsOnly(f[0]) || !DigitsOnly(f[1]))
         return Fail("invalid signal CSV");
      long opened=StringToInteger(f[0]); int position=-1;
      for(int j=0;j<count;j++) if((long)rates[j].time==opened) { position=j; break; }
      if(position<0 || opened<=previous || StringToInteger(f[1])!=opened+900 ||
         (f[2]!="LONG" && f[2]!="SHORT" && f[2]!="NO_TRADE") ||
         f[3]!=m[16] || f[4]!=m[17] || f[5]!=m[18] || f[7]!=m[14])
         return Fail("signal timestamp/model/provenance mismatch");
      signal_times[i]=opened; actions[i]=f[2]; bar_positions[i]=position; previous=opened;
   }
   if(previous!=latest) return Fail("missing latest signal");
   if(m[13]==last_run && display_chart>0 && ChartSymbol(display_chart)==custom_symbol &&
      ChartPeriod(display_chart)==PERIOD_M15)
   { Banner("VALID "+m[16]+"@"+m[17]+" last UTC "+TimeToString((datetime)latest),clrSilver); return true; }
   // Only after complete validation may the custom history/chart be touched.
   custom_symbol="EXT_"+StringSubstr(ExpectedProduct,0,3)+"_"+StringSubstr(CommonSubfolder,StringLen(CommonSubfolder)-16,8);
   bool custom=false;
   if(!SymbolExist(custom_symbol,custom))
   { if(!CustomSymbolCreate(custom_symbol,"Metatr External")) return Fail("custom symbol create failed"); }
   else if(!custom) return Fail("refuse existing broker symbol");
   if(!CustomSymbolSetInteger(custom_symbol,SYMBOL_TRADE_MODE,SYMBOL_TRADE_MODE_DISABLED) ||
      !CustomSymbolSetInteger(custom_symbol,SYMBOL_DIGITS,8) ||
      !CustomSymbolSetString(custom_symbol,SYMBOL_DESCRIPTION,"EXTERNAL SIMULATED "+expected))
      return Fail("custom symbol properties failed");
   if(!SymbolSelect(custom_symbol,true)) return Fail("custom symbol select failed");
   // MT5 stores custom history as M1 anchors: do not fabricate missing minute candles.
   if(CustomRatesReplace(custom_symbol,rates[0].time,rates[count-1].time,rates,count)!=count)
      return Fail("custom rates update failed");
   if(display_chart<=0 || ChartSymbol(display_chart)!=custom_symbol)
   {
      display_chart=FindDisplayChart();
      if(display_chart<=0) display_chart=ChartOpen(custom_symbol,PERIOD_M15);
   }
   if(display_chart<=0) return Fail("open M15 external chart manually and retry");
   ChartSetSymbolPeriod(display_chart,custom_symbol,PERIOD_M15);
   int first=(int)MathMax(0,signals-500);
   for(int i=first;i<signals;i++)
   {
      int pos=bar_positions[i]; string name=PREFIX+"signal_"+IntegerToString(signal_times[i]);
      bool flat=(actions[i]=="NO_TRADE");
      double price=flat ? rates[pos].close : (actions[i]=="LONG" ? rates[pos].low : rates[pos].high);
      ENUM_OBJECT kind=flat ? OBJ_TEXT : OBJ_ARROW;
      if(ObjectFind(display_chart,name)>=0 &&
         (ENUM_OBJECT)ObjectGetInteger(display_chart,name,OBJPROP_TYPE)!=kind)
         ObjectDelete(display_chart,name);
      if(ObjectFind(display_chart,name)<0)
      { if(!ObjectCreate(display_chart,name,kind,0,(datetime)signal_times[i],price)) return Fail("marker creation failed"); }
      else if(!ObjectMove(display_chart,name,0,(datetime)signal_times[i],price)) return Fail("marker move failed");
      ObjectSetInteger(display_chart,name,OBJPROP_COLOR,flat ? clrGray : (actions[i]=="LONG" ? clrLime : clrTomato));
      if(flat) ObjectSetString(display_chart,name,OBJPROP_TEXT,"NO_TRADE");
      else ObjectSetInteger(display_chart,name,OBJPROP_ARROWCODE,actions[i]=="LONG" ? 233 : 234);
      ObjectSetString(display_chart,name,OBJPROP_TOOLTIP,actions[i]+" research "+m[16]+"@"+m[17]+" run "+m[13]);
   }
   for(int j=ObjectsTotal(display_chart)-1;j>=0;j--)
   {
      string existing=ObjectName(display_chart,j);
      if(StringFind(existing,PREFIX+"signal_")!=0) continue;
      bool keep=false;
      for(int i=first;i<signals;i++)
         if(existing==PREFIX+"signal_"+IntegerToString(signal_times[i])) { keep=true; break; }
      if(!keep) ObjectDelete(display_chart,existing);
   }
   last_run=m[13];
   Print("EXTERNAL VALID ",expected," bars=",count," signals=",signals," run=",last_run," execution=DISABLED");
   Banner("VALID "+m[16]+"@"+m[17]+" latest UTC "+TimeToString((datetime)latest),clrSilver);
   return true;
}

int OnInit()
{
   if(PollSeconds<1 || PollSeconds>60 || MaximumBars<1 || MaximumBars>1000 ||
      MaximumAgeBars<1 || MaximumAgeBars>100 || StringLen(CommonSubfolder)!=23 ||
      StringSubstr(CommonSubfolder,0,7)!="metatr\\" ||
      (StringSubstr(ExpectedProduct,0,4)!="BIP-" && StringSubstr(ExpectedProduct,0,4)!="BIT-"))
      return INIT_PARAMETERS_INCORRECT;
   string suffix=StringSubstr(CommonSubfolder,7);
   for(int i=0;i<16;i++)
   { ushort c=StringGetCharacter(suffix,i); if(!((c>='0' && c<='9') || (c>='a' && c<='f'))) return INIT_PARAMETERS_INCORRECT; }
   custom_symbol="EXT_"+StringSubstr(ExpectedProduct,0,3)+"_"+StringSubstr(CommonSubfolder,StringLen(CommonSubfolder)-16,8);
   display_chart=FindDisplayChart();
   if(!EventSetTimer(PollSeconds)) return INIT_FAILED;
   Banner("STALE awaiting verified snapshot",clrOrange);
   return INIT_SUCCEEDED;
}

void OnTimer() { if(!Refresh()) Banner("STALE/INVALID "+error_reason,clrOrangeRed); }
void OnDeinit(const int reason)
{
   EventKillTimer(); Comment("");
   if(display_chart>0) ObjectsDeleteAll(display_chart,PREFIX);
}
