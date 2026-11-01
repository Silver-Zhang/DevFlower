// A rejection the server definitely answered (4xx) created no turn, so the
// composer may stay editable. Only a lost reply or a server fault leaves the
// outcome unknown and needs the read-only recovery path.
export function sendFailureIsAmbiguous(status){
 if(status===undefined||status===null)return true;
 if(typeof status!=='number'||!Number.isFinite(status))return true;
 return status>=500;
}
