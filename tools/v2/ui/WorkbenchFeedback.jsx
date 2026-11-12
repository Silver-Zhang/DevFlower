import React from 'react';

export function PanelEmpty({icon:Icon,title,children,actions}){
 return <section className="panel-empty">
  <span className="panel-empty-icon"><Icon size={20} aria-hidden="true"/></span>
  <h3>{title}</h3>
  <div>{children}</div>
  {actions&&<div className="panel-empty-actions">{actions}</div>}
 </section>;
}
