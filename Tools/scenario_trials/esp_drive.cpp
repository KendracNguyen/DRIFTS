// ESP32 side with the REAL WeaveDetector (weave.h) and AlertScore (alertness.h), firmware thresholds from config.h.
#include <cstdio>
#include <cstring>
#include <cmath>
#include <deque>
#include "weave.h"
#include "alertness.h"
static float sd(const std::deque<float>& v){ if(v.size()<2) return 0; double m=0; for(float x:v) m+=x; m/=v.size(); double s=0; for(float x:v) s+=(x-m)*(x-m); return (float)std::sqrt(s/(v.size()-1)); }
int main(){
  WeaveDetector weave(2.0f, 2000); AlertScore sc; std::deque<unsigned long> rev, brk; std::deque<float> rpm, spd, thr;
  bool alerting=false, scoreAlert=false; unsigned long alertStart=0, nextTick=1000; char c[16]; unsigned long ms;
  while(scanf("%lu %15s",&ms,c)==2){
    while(nextTick<=ms){
      while(!rev.empty() && nextTick-rev.front()>60000) rev.pop_front();
      while(!brk.empty() && nextTick-brk.front()>60000) brk.pop_front();
      DrivingRatios d; d.steer=rev.size()/6.0f; d.brake=brk.size()/3.0f;
      if(rpm.size()>=10){ d.rpm=sd(rpm)/400.0f; d.speed=sd(spd)/8.0f; d.throttle=sd(thr)/15.0f; }
      sc.update(d,nextTick);
      if(alerting && nextTick-alertStart>20000){ alerting=false; scoreAlert=false; }
      bool low=sc.level()==AlertLevel::Low;
      if(low && !alerting){ alerting=true; scoreAlert=true; alertStart=nextTick; printf("%lu ALERT score_low\n",nextTick); }
      else if(!low && scoreAlert && alerting){ alerting=false; scoreAlert=false; }
      printf("%lu TICK %.3f %.3f %.3f %.3f %.3f %.1f %.1f %.1f %s %d\n",nextTick,d.steer,d.brake,d.rpm,d.speed,d.throttle,sc.eye(),sc.driving(),sc.score(),alertLevelName(sc.level()),alerting?1:0);
      nextTick+=1000;
    }
    if(!strcmp(c,"YAW")){ float v; if(scanf("%f",&v)==1 && weave.feed(v,ms)) rev.push_back(ms); }
    else if(!strcmp(c,"BRAKE")) brk.push_back(ms);
    else if(!strcmp(c,"OBD")){ float a,b,t; if(scanf("%f %f %f",&a,&b,&t)==3){ rpm.push_back(a); spd.push_back(b); thr.push_back(t); if(rpm.size()>30){rpm.pop_front();spd.pop_front();thr.pop_front();} } }
    else if(!strcmp(c,"EYE")){ float v; if(scanf("%f",&v)==1) sc.setEye(v,ms); }
    else if(!strcmp(c,"DROWSY")){ sc.onPiResult(true,ms); if(!alerting){alerting=true;scoreAlert=false;alertStart=ms;printf("%lu ALERT pi_drowsy\n",ms);} }
    else if(!strcmp(c,"NOT_DROWSY")){ sc.onPiResult(false,ms); if(alerting){alerting=false;scoreAlert=false;} }
    else if(!strcmp(c,"END")) break;
  }
}
