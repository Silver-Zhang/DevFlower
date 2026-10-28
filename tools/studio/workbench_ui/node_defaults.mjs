// Only used when creating a node. Existing drafts keep their explicit timeout.
export function profileTimeout(profile){
 const value=profile?.timeout_seconds;
 return Number.isInteger(value)?Math.min(3600,Math.max(10,value)):300;
}
