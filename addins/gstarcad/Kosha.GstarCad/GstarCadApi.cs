// SPDX-License-Identifier: Apache-2.0
// The only place that names GStarCAD's .NET namespaces. GStarCAD's interface follows AutoCAD's closely;
// the versions checked use the GrxCAD.* namespaces. If your SDK version uses different namespaces,
// change them here and nowhere else. The shared DWG code (sdk/dotnet/Kosha.CadCore.Dwg) is written
// against these names, and the AutoCAD add-in's AutoCadApi.cs maps the same names to Autodesk.AutoCAD.*.
// ApplicationServices is also imported as a whole, so DocumentManager.Open and Document.CloseAndDiscard
// are found whether the SDK has them as methods or, like AutoCAD, as extension methods.
global using CadApplication = GrxCAD.ApplicationServices.Application;
global using CadDocumentNative = GrxCAD.ApplicationServices.Document;
global using GrxCAD.ApplicationServices;
global using GrxCAD.DatabaseServices;
global using GrxCAD.Runtime;
global using GrxCAD.Windows;
