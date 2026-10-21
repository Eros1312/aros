import type {Metadata} from 'next';
import './globals.css';
import './workspace.css';
import './reference-home.css';
import './reference-brand.css';
import './workflow.css';
export const metadata:Metadata={title:'Aros. — Tu espacio de trabajo',description:'Tu espacio personal para aprendizaje, proyectos y vida diaria.',icons:{icon:'/icon.svg'}};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="es"><body>{children}</body></html>}
