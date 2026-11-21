async function openReaderDisclosure(reader, label) {
 const summary = reader.locator('summary').getByText(label, {exact: true});
 await summary.waitFor();
 if (!await summary.evaluate(element => element.closest('details').open)) await summary.click();
}
module.exports = {openReaderDisclosure};
