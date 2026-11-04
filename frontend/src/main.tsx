import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/tokens.css'
import './styles/app.css'
import { authConfig } from './boot.ts'
import Root from './Root.tsx'

const root = createRoot(document.getElementById('root')!)

void authConfig().then((auth) =>
  root.render(
    <StrictMode>
      <Root auth={auth} />
    </StrictMode>,
  ),
)
