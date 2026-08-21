import { useEffect, useState } from 'react';
import './App.css';
import DeskHome from './components/DeskHome';
import RenewalCard from './components/RenewalCard';
import NeedsVerification from './components/NeedsVerification';
import AmsQueue from './components/AmsQueue';
import CrmShell from './components/CrmShell';
import { listenCrmWidget, parseRoute, writeRoute } from './crmLaunch';

function App() {
  const [route, setRoute] = useState(parseRoute);
  const [banner, setBanner] = useState('');

  useEffect(() => {
    const onHash = () => setRoute(parseRoute());
    window.addEventListener('hashchange', onHash);
    if (!window.location.hash && !window.location.search) window.location.hash = '#/';
    const stop = listenCrmWidget((id, entity) => {
      if (!entity || /renewal/i.test(entity)) {
        writeRoute({ view: 'card', id });
        setRoute({ view: 'card', id });
      }
    });
    return () => {
      window.removeEventListener('hashchange', onHash);
      if (typeof stop === 'function') stop();
    };
  }, []);

  function onNavigate(next) {
    writeRoute(next);
    setRoute({ ...parseRoute(), ...next });
  }

  return (
    <CrmShell route={route} onNavigate={onNavigate} banner={banner} setBanner={setBanner}>
      {route.view === 'card' ? (
        <RenewalCard id={route.id} onNavigate={onNavigate} setBanner={setBanner} />
      ) : null}
      {route.view === 'needs' ? <NeedsVerification /> : null}
      {route.view === 'ams-pending' || route.view === 'ams-failed' ? (
        <AmsQueue view={route.view} setBanner={setBanner} />
      ) : null}
      {route.view === 'desk' ? (
        <DeskHome route={route} onNavigate={onNavigate} />
      ) : null}
    </CrmShell>
  );
}

export default App;
