async function openWorkflowActions(page) {
 const menu=page.locator('.canvas-more-actions');
 await menu.waitFor();
 if(!await menu.evaluate(element=>element.open))await menu.locator('summary').click();
}
module.exports={openWorkflowActions};
