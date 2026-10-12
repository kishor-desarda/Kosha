// SPDX-License-Identifier: Apache-2.0
// The only place that names GStarCAD's .NET namespaces. GStarCAD's interface follows AutoCAD's closely;
// the versions checked use the GrxCAD.* namespaces. If your SDK version uses different namespaces,
// change them here and nowhere else. (The AutoCAD add-in is the same code with Autodesk.AutoCAD.* here.)
global using CadApplication = GrxCAD.ApplicationServices.Application;
global using CadDocumentNative = GrxCAD.ApplicationServices.Document;
global using GrxCAD.DatabaseServices;
global using GrxCAD.Runtime;
global using GrxCAD.Windows;
